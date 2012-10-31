import unittest
import dhdl

class BaseTest(unittest.TestCase):
    def sliceFromStr(self, s):
        return dhdl.Slice.fromAST(dhdl.slicedef.parseString(s)[0])

class ParserTest(BaseTest):
    def test_bus(self):
        self.assertIsInstance(dhdl.bus.parseString("bus1")[0],
            dhdl.BusIdentifier)
    
    def test_expr(self):
        expr = dhdl.expr.parseString("a | b & c ^ d ^ e")[0]
        self.assertIsInstance(expr, dhdl.AndExpression)
        self.assertEquals(2, len(expr.args))
        self.assertIsInstance(expr.args[0], dhdl.OrExpression)
        self.assertIsInstance(expr.args[1], dhdl.XorExpression)
        self.assertEquals(2, len(expr.args[0].args))
        self.assertEquals("a", expr.args[0].args[0])
        self.assertEquals("b", expr.args[0].args[1])
        self.assertEquals(3, len(expr.args[1].args))
        self.assertEquals("c", expr.args[1].args[0])
        self.assertEquals("d", expr.args[1].args[1])
        self.assertEquals("e", expr.args[1].args[2])
    
    def test_assignment(self):
        assign = dhdl.assignment.parseString("a | b -> c")[0]
        self.assertIsInstance(assign, dhdl.AssignmentStatement)
        self.assertEquals(list(assign.buses), ["c"])
        self.assertEquals(assign.async, True)
        self.assertIsInstance(assign.expr, dhdl.OrExpression)
        
        assign = dhdl.assignment.parseString("a sync -> b -> c")[0]
        self.assertEquals(assign.async, False)
        self.assertEquals(list(assign.buses), ["b", "c"])
    
    def test_buseq(self):
        buseq = dhdl.buseq.parseString("a <-> b")[0]
        self.assertIsInstance(buseq, dhdl.BusSwitchStatement)
        self.assertEquals(["a", "b"], buseq.buses)
    
    def test_slicedef(self):
        testdef = "slice foo { a1 & b1 -> c1; a1 <-> d1; }"
        slice = dhdl.slicedef.parseString(testdef)[0]
        self.assertIsInstance(slice, dhdl.SliceDefinition)
        self.assertEquals("foo", slice.name)
        self.assertEquals(2, len(slice.statements))
        
    def test_literal(self):
        testdef = "slice foo { 0 -> a1; 1 -> b1; }"
        slice = dhdl.slicedef.parseString(testdef)[0]
        self.assertIsInstance(slice.statements[0].expr, dhdl.BooleanLiteral)
        self.assertEquals(False, slice.statements[0].expr.value)
        self.assertIsInstance(slice.statements[1].expr, dhdl.BooleanLiteral)
        self.assertEquals(True, slice.statements[1].expr.value)

    def test_not(self):
        dhdl.unaryexp.parseString("!a")
        testdef = "slice foo { !(a & b) -> c; }"
        slice = dhdl.slicedef.parseString(testdef)[0]
        self.assertIsInstance(slice.statements[0].expr, dhdl.NotExpression)
        self.assertIsInstance(slice.statements[0].expr.args[0], dhdl.AndExpression)

    def test_all_busids(self):
        testdef = "slice foo { (l0 & u0) | u0 -> r0; }"
        slice = dhdl.slicedef.parseString(testdef)[0]
        self.assertEquals(set(['l0', 'u0']), slice.statements[0].expr.allBusIds())
        
class SliceDefTest(BaseTest):
    def test_defaults(self):
        testdef = "slice foo {}"
        slice = self.sliceFromStr(testdef)
        self.assertEquals([0] * 3, slice.input_muxes)
        self.assertEquals([[False] * 2, [False] * 2], slice.output_enables)
        self.assertEquals([[False] * 8, [False] * 8], slice.luts)
        self.assertEquals([True] * 2, slice.asyncs)
        self.assertEquals([True] * 4, slice.bus_switches.values())

    def test_switches(self):
        testdef = "slice foo { l1 </> r1; }"
        slice = self.sliceFromStr(testdef)
        self.assertEquals(False, slice.bus_switches[('l1', 'r1')])
        
    def test_luts(self):
        testdef = "slice foo { l0 ^ u0 ^ r1 -> r0;}"
        slice = self.sliceFromStr(testdef)
        self.assertEquals([False, True, True, False, True, False, False, True],
                          slice.luts[1])
    
    def test_lut_order(self):
        testdef = "slice foo { l0 -> r0; u0 -> d0; }"
        slice = self.sliceFromStr(testdef)
        self.assertEquals([False, True, False, True, False, True, False, True],
                          slice.luts[1])
        self.assertEquals([False, False, True, True, False, False, True, True],
                          slice.luts[0])

    def test_input_muxes(self):
        testdef = "slice foo { l1 -> r1; u1 -> d1; }"
        slice = self.sliceFromStr(testdef)
        self.assertEquals([1, 1, 0], slice.input_muxes)

    def test_output_enables(self):
        testdef = "slice foo { l1 -> r0 -> r1; }"
        slice = self.sliceFromStr(testdef)
        self.assertEquals([[False, False], [True, True]], slice.output_enables)
        
    def test_asyncs(self):
        testdef = "slice foo { l0 sync -> r0; u0 sync -> d0; }"
        slice = self.sliceFromStr(testdef)
        self.assertEquals([False, False], slice.asyncs)
    
    def test_switch_defaults(self):
        testdef = "slice foo { l0 -> r1; }"
        slice = self.sliceFromStr(testdef)
        self.assertEquals(True, slice.bus_switches[('l0', 'r0')])
        self.assertEquals(False, slice.bus_switches[('l1', 'r1')])
    
    def test_literals(self):
        testdef = "slice foo { 1 -> r0; }"
        slice = self.sliceFromStr(testdef)
        self.assertEquals([True] * 8, slice.luts[1])
       
    def test_not(self):
        testdef = "slice foo { !l0 -> r0; }"
        slice = self.sliceFromStr(testdef)
        self.assertEquals([True, False] * 4, slice.luts[1])

class SliceCompileTest(BaseTest):
    def test_defaults(self):
        testdef = "slice foo { }"
        compiled = self.sliceFromStr(testdef).compile()
        self.assertEquals("561e0000", compiled.encode('hex'))

    def test_output_enables(self):
        testdef = "slice foo { l0 -> r0 -> r1; l0 -> d0 -> d1; }"
        compiled = self.sliceFromStr(testdef).compile()
        self.assertEquals("2e00aaaa", compiled.encode('hex'))
    
    def test_input_muxes(self):
        testdef = "slice foo { l1 -> r0; }"
        compiled = self.sliceFromStr(testdef).compile()
        self.assertEquals("763c00aa", compiled.encode('hex'))
        testdef = "slice foo { r1 -> r0; }"
        compiled = self.sliceFromStr(testdef).compile()
        self.assertEquals("769c00f0", compiled.encode('hex'))
    
    def test_switches(self):
        testdef = "slice foo { l0 </> r0; u1 </> d1; }"
        compiled = self.sliceFromStr(testdef).compile()
        self.assertEquals("560c0000", compiled.encode('hex'))
    
    def test_asyncs(self):
        testdef = "slice foo { l0 sync -> r0; u0 sync -> d0; }"
        compiled = self.sliceFromStr(testdef).compile()
        self.assertEquals("7814ccaa", compiled.encode('hex'))

class CompilerTest(BaseTest):
    def test_invoke_slices(self):
        testdef = "slice a {} slice b {} a b, b a"
        ast = dhdl.parser.parseString(testdef)
        slice_dict = dict((slice.name, slice) for slice in ast['slices'])
        slices = dhdl.invoke_slices(slice_dict, ast['invocations'])
        self.assertEqual("a", slices[0][0].name)
        self.assertEqual("b", slices[0][1].name)
        self.assertEqual("b", slices[1][0].name)
        self.assertEqual("a", slices[1][1].name)
        
    def test_compile(self):
        testdef = "slice a {} slice b { l0 </> r0; } a b, a b"
        compiled = dhdl.compile(testdef)
        self.assertEqual("561e0000561c0000561c0000561e0000",
                         compiled.encode('hex'))
    
if __name__ == "__main__":
    unittest.main()
