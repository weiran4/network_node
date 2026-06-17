import unittest
from pathlib import Path


class FrontendOptimizedReuseTests(unittest.TestCase):
    def test_optimized_request_reuses_reduced_dependency_cache(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("async function ensureReducedResult", source)
        self.assertIn("function reducedDependencyAnalysisFromResult", source)
        self.assertIn("reduced_dependency_analysis", source)
        self.assertIn("await ensureReducedResult(basePayload)", source)

    def test_optimized_payload_includes_direct_retained_stamps(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("function directRetainedStampsForPayload", source)
        self.assertIn("direct_retained_stamps: directRetainedStampsForPayload", source)
        self.assertIn("direct_retained_stamps: payload.direct_retained_stamps", source)
        self.assertIn("Gred_direct", source)

    def test_right_panel_has_resizable_width_controls(self):
        source = Path("index.html").read_text(encoding="utf-8")

        self.assertIn("id=\"panelResizeBar\"", source)
        self.assertIn("--panel-width", source)
        self.assertIn("function applyPanelWidth", source)
        self.assertIn("function startPanelResize", source)
        self.assertIn("panelResizeBar.addEventListener(\"pointerdown\", startPanelResize)", source)


if __name__ == "__main__":
    unittest.main()
