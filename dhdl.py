#!/usr/bin/env python2.7

import argparse
import logging
import operator
import struct
import sys
from pyparsing import Group, oneOf, Suppress, Word, alphas, alphanums, nums, Forward, ZeroOrMore, ParseResults, oneOf, OneOrMore, Optional, lineno, col

class ASTNode(object):
    def __init__(self, loc, args):
        self.args = list(args)
        self.loc = loc
    
    def __repr__(self):
        return "%s(%s)" % (type(self).__name__, ", ".join(repr(x) for x in self.args))
    
    def allBusIds(self):
        ret = set()
        for arg in self.args:
            ret.update(arg.allBusIds())
        return ret

class BusIdentifier(ASTNode):
    def __init__(self, loc, args):
        self.name = args[0]
    
    def __repr__(self):
        return repr(self.name)

    def __str__(self):
        return self.name
        
    def __call__(self, inputs):
        return inputs[self.name]
    
    def __hash__(self):
        return hash(self.name)
    
    def __cmp__(self, other):
        if isinstance(other, BusIdentifier):
            other = other.name
        return cmp(self.name, other)
    
    def allBusIds(self):
        return set([self])

class BooleanLiteral(ASTNode):
    def __init__(self, loc, args):
        super(BooleanLiteral, self).__init__(loc, args)
        self.value = args[0] == "1"
    
    def __repr__(self):
        return self.value
        
    def __call__(self, inputs):
        return self.value
    
    def allBusIds(self):
        return set()

class NotExpression(ASTNode):
    def __init__(self, loc, args):
        args = args[1:]
        super(NotExpression, self).__init__(loc, args)
        
    def __call__(self, inputs):
        return not self.args[0](inputs)
        
class OrExpression(ASTNode):
    def __call__(self, inputs):
        return reduce(operator.__or__, (x(inputs) for x in self.args), False)

class AndExpression(ASTNode):
    def __call__(self, inputs):
        return reduce(operator.__and__, (x(inputs) for x in self.args), True)

class XorExpression(ASTNode):
    def __call__(self, inputs):
        return reduce(operator.__xor__, (x(inputs) for x in self.args), False)

class AssignmentStatement(ASTNode):
    def __init__(self, loc, args):
        super(AssignmentStatement, self).__init__(loc, args)
        self.expr = args['expr'][0]
        self.buses = list(args['buses'])
        self.async = args.get('sync', 'async') == 'async'

class BusSwitchStatement(ASTNode):
    def __init__(self, loc, args):
        super(BusSwitchStatement, self).__init__(loc, args)
        self.value = args['op'] == '<->'
        self.buses = [args['busa'], args['busb']]

class SliceDefinition(ASTNode):
    def __init__(self, loc, args):
        super(SliceDefinition, self).__init__(loc, args)
        self.name = args['name']
        self.statements = args.get('body', [])

def toAST(klass, minargs=1, **kwargs):
    def action(s, loc, toks):
        if len(toks) >= minargs:
            return [klass(loc, toks, **kwargs)]
        else:
            return [toks[0]]
    return action

ident = Word(alphas, alphanums+"_")
bus = Word(alphas, alphanums).setParseAction(toAST(BusIdentifier))
literal = oneOf("0 1").setParseAction(toAST(BooleanLiteral))
expr = Forward()
atom = bus | literal | (Suppress("(") + expr + Suppress(")"))
unaryexp = (Optional("!") + atom).setParseAction(toAST(NotExpression, minargs=2))
orexp = (unaryexp + ZeroOrMore(Suppress("|") + atom)).setParseAction(toAST(OrExpression, minargs=2))
xorexp = Forward()
xorexp << (orexp + ZeroOrMore(Suppress("^") + orexp)).setParseAction(toAST(XorExpression, minargs=2))
andexp = Forward()
andexp << (xorexp + ZeroOrMore(Suppress("&") + xorexp)).setParseAction(toAST(AndExpression, minargs=2))
expr << andexp
assignment = (expr("expr") + Optional(oneOf("sync async"))("sync") + OneOrMore(Suppress("->") + bus)("buses")).setParseAction(toAST(AssignmentStatement))
buseq = (bus("busa") + oneOf("<-> </>")("op") + bus("busb")).setParseAction(toAST(BusSwitchStatement))
statement = (assignment ^ buseq)
statements = ZeroOrMore(Optional(statement) + Suppress(";"))
slicedef = (Suppress("slice") + ident("name") + Suppress("{") + statements("body") + Suppress("}")).setParseAction(toAST(SliceDefinition))

invocline = Group(OneOrMore(ident))
invocation = Group(invocline + ZeroOrMore(Suppress(",") + invocline))

parser = ZeroOrMore(slicedef)("slices") + invocation("invocations")

inputs = (('l1', 'l0'), ('u0', 'u1'), ('r1', 'r0'))
num_luts = 2
outputs = (('d1', 'd0'), ('r0', 'r1'))
switches = [('l0', 'r0'), ('l1', 'r1'), ('d1', 'u1'), ('d0', 'u0')]

class SliceConfigurationException(Exception):
    def __init__(self, loc, message, *args):
        super(SliceConfigurationException, self).__init__(message % args)
        self.loc = loc

    def __str__(self):
        return "%s: %d %s" % (type(self).__name__, self.loc, self.message)

def SliceInvocationException(Exception): pass
        
def packBools(b):
    # Packs booleans into an int, LSB first
    return reduce(operator.__add__, (int(x) << i for i, x in enumerate(b)))
        
class Slice(object):
    def __init__(self, name):
        self.name = name
        self.input_muxes = [None] * len(inputs)
        self.output_enables = [[False for i in range(len(o))] for o in outputs]
        self.lut_expressions = [lambda vars: False] * num_luts
        self.luts = [None] * num_luts
        self.asyncs = [True] * num_luts
        self.bus_switches = dict((k, None) for k in switches)
        self.bus_switch_defaults = dict((k, True) for k in switches)

    @classmethod
    def fromAST(cls, ast):
        slice = cls(ast.name)
        for statement in ast.statements:
            if isinstance(statement, AssignmentStatement):
                slice._parseAssignment(statement)
            elif isinstance(statement, BusSwitchStatement):
                slice._parseBusSwitch(statement)
            else:
                raise SliceConfigurationException("Unrecognized statement: %s", statement)
        slice._setDefaults()
        slice._generateLUTs()
        return slice
        
    def _pickLUT(self, stmt):
        for lut_id, outs in enumerate(outputs):
            if set(outs).issuperset(stmt.buses):
                for out_idx, out in enumerate(outs):
                    if out in stmt.buses:
                        self.output_enables[lut_id][out_idx] = True
                        for switch in self.bus_switches:
                            if out in switch:
                                self.bus_switch_defaults[switch] = False
                self.lut_expressions[lut_id] = stmt.expr
                return lut_id
        raise SliceConfigurationException(stmt.loc, "No LUT is capable of outputting to all of %r", stmt.buses)

    def _assignInputs(self, stmt):
        ins = stmt.expr.allBusIds()
        for bus_name in ins:
            for input_idx, buses in enumerate(inputs):
                if bus_name in buses:
                    if self.input_muxes[input_idx] in (None, buses.index(bus_name)):
                        self.input_muxes[input_idx] = buses.index(bus_name)
                        break   
            else:
                raise SliceConfigurationException(stmt.loc, "Cannot find appropriate input mux setting for %r", bus_name)
                
    def _parseAssignment(self, stmt):
        lut_id = self._pickLUT(stmt)
        if self.luts[lut_id] is not None:
            raise SliceConfigurationException(stmt.loc, "Statement requires a LUT that is already in use.")
        self.asyncs[lut_id] = stmt.async
        
        self._assignInputs(stmt)
        
    def _parseBusSwitch(self, stmt):
        switch_name = tuple(sorted(stmt.buses))
        if switch_name not in switches:
            raise SliceConfigurationException(stmt.loc, "Cannot connect bus lines %s and %s", *stmt.buses)
        elif self.bus_switches[switch_name] is not None:
            raise SliceConfigurationException(
                stmt.loc, "Duplicate definition for bus switch between %s and %s", *stmt.buses)
        self.bus_switches[switch_name] = stmt.value
        
    def _setDefaults(self):
        self.input_muxes = [x or 0 for x in self.input_muxes]
        self.asyncs = [x if x is not None else True for x in self.asyncs]
        for k, v in self.bus_switches.items():
            self.bus_switches[k] = v if v is not None else self.bus_switch_defaults[k]

    def _generateLUTs(self):
        for lut_id in range(num_luts):
            lut = []
            for idx in range(2**len(inputs)):
                vars = dict((inputs[i][self.input_muxes[i]], idx & (1 << i) != 0) for i in range(len(inputs)))
                lut.append(self.lut_expressions[lut_id](vars))
            self.luts[lut_id] = lut
            
    def compile(self):
        async_oe = packBools([False] + self.asyncs +
                             [self.output_enables[0][0], not self.output_enables[0][1],
                              self.output_enables[1][0], not self.output_enables[1][1]])
        switch_mux = packBools([False] + [self.bus_switches[x] for x in switches] +
                               self.input_muxes)
        lut_0 = packBools(self.luts[0])
        lut_1 = packBools(self.luts[1])
        return struct.pack('BBBB', async_oe, switch_mux, lut_1, lut_0)

def invoke_slices(ns, invocations):
    rows = []
    width = None
    for invoc_row in invocations:
        rows.append([ns[x] for x in invoc_row])
        width = width or len(invoc_row)
        if width != len(invoc_row):
            raise SliceInvocationException("Slice invocations must be a rectangular array.")
    return rows

def compile(s):
    ast = parser.parseString(s)
    slice_dict = dict((slice.name, Slice.fromAST(slice)) for slice in ast['slices'])
    slices = invoke_slices(slice_dict, ast['invocations'])
    
    compiled = []
    for rownum, row in enumerate(reversed(slices)):
        if rownum % 2 == 1:
            row = reversed(row)
        for slice in row:
            compiled.append(slice.compile())
    return ''.join(compiled)

argparser = argparse.ArgumentParser(description="Compiles DHDL definitions")
argparser.add_argument('infile', metavar='INFILE', nargs='?', default='-')
argparser.add_argument('outfile', metavar='OUTFILE', nargs='?', default='-')
    
def main():
    args = argparser.parse_args()
    if args.infile == '-':
        infile = sys.stdin
    else:
        infile = open(args.infile, 'rb')
    if args.outfile == '-':
        outfile = sys.stdout
    else:
        outfile = open(args.outfile, 'wb')
    
    outfile.write(compile(infile.read()).encode('hex'))
    

if __name__ == '__main__':
    main()