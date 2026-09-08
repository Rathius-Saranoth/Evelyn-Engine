# test_notation_detection.py
# date created: 2026-09-08 18:10:00
# date modified: 2026-09-08 18:39:14
# tags: #tests, #notation, #music, #math, #chemistry, #sanitization

"""Unit tests for multi-discipline notation leak detection in string_utils.py."""

import unittest

from Evelyn.tools.string_utils import (
    clean_title,
    detect_notation_discipline,
    is_notation_leak,
)


class TestNotationDetection(unittest.TestCase):
    """Test suite for detect_notation_discipline and is_notation_leak."""

    def test_music_notation_glyphs_and_keywords(self):
        music_samples = [
            "35 - sharp-sharp œ œ œ œ œ œ œ œ œ œ œ œ œ œ œ œ œ œ œ œ w",
            "20 - sharp œ œ ˙ œ œ œ œ œ œ ˙",
            "11 - w wsharp w w w",
            "04 - ˙",
            "06 - ˙ Ó",
            "07 - œ Œ",
            "21 - sharp-sharp .. .. œ œ œ œ œ œ œ œ œ œ œ œ",
            "14 - .. œ œ ˙ œ œ ˙ œ œ œ œ œ œ œ œ œ œ ˙",
            "32 - ˙ ˙ w ˙ ˙ ˙ ˙ w",
            "♩ ♪ ♫ ♬ ♭ ♮ ♯",
            "47 - q k e",
            "53 - Q Q Q",
            "54 - 2 4",
        ]
        for s in music_samples:
            self.assertTrue(
                is_notation_leak(s),
                f"Expected notation leak detected for '{s}'",
            )
            disc = detect_notation_discipline(s)
            self.assertIn(
                disc,
                ("music", "formatting_artifact"),
                f"Discipline for '{s}' was {disc}",
            )

    def test_latex_math_detection(self):
        latex_samples = [
            r"\frac{d}{dx} \int_0^x f(t) dt = f(x)",
            r"$\sum_{i=1}^n x_i^2 = 1$",
            r"$$\int_{-\infty}^{\infty} e^{-x^2} dx = \sqrt{\pi}$$",
            r"\partial u / \partial t = \alpha \nabla^2 u",
            r"\begin{matrix} 1 & 0 \\ 0 & 1 \end{matrix}",
        ]
        for s in latex_samples:
            self.assertTrue(is_notation_leak(s), f"Expected LaTeX leak for '{s}'")
            self.assertEqual(
                detect_notation_discipline(s),
                "latex_math",
                f"Discipline for '{s}' should be latex_math",
            )

    def test_dense_math_operators(self):
        math_samples = [
            "∂f/∂x + ∂f/∂y = 0",
            "∀x ∈ S, ∃y: x ≤ y",
            "A ∪ B ⊆ C ∩ D",
        ]
        for s in math_samples:
            self.assertTrue(is_notation_leak(s), f"Expected math operator leak for '{s}'")
            self.assertEqual(
                detect_notation_discipline(s),
                "math_operators",
                f"Discipline for '{s}' should be math_operators",
            )

    def test_chemistry_stoichiometric_equations(self):
        chem_samples = [
            "2H2 + O2 -> 2H2O",
            "CH4 + 2O2 -> CO2 + 2H2O",
            "N2 + 3H2 <=> 2NH3",
            "HCl + NaOH → NaCl + H2O",
        ]
        for s in chem_samples:
            self.assertTrue(is_notation_leak(s), f"Expected chemistry leak for '{s}'")
            self.assertEqual(
                detect_notation_discipline(s),
                "chemistry",
                f"Discipline for '{s}' should be chemistry",
            )

    def test_formatting_artifacts(self):
        artifact_samples = [
            "|---|---|---|",
            "=====",
            "------",
            ".. ..",
            "note_title.md",
            "document.pdf",
            "USING THE REFRIGERATOR \ufffd\ufffd\ufffd\ufffd\ufffd\ufffd",
            "Rou\ufffdne Maintenance",
        ]
        for s in artifact_samples:
            self.assertTrue(is_notation_leak(s), f"Expected formatting artifact for '{s}'")
            self.assertEqual(
                detect_notation_discipline(s),
                "formatting_artifact",
                f"Discipline for '{s}' should be formatting_artifact",
            )

    def test_false_positive_guards(self):
        safe_samples = [
            "Introduction to Cello",
            "Twinkle Twinkle Little Star",
            "Performance (±5% Variance)",
            "Revenue in 2026 was $50M, up from $19.99 per unit",
            "Step 1 -> Step 2 -> Step 3",
            "ChromaDB -> SQLite Sync Architecture",
            "The Cello Suite No. 1 in G Major",
            "Chapter 4: Quantum Mechanics & Wave Functions",
            "Notes on C++ Programming",
            "Special Report: $100 Billion Valuation",
        ]
        for s in safe_samples:
            disc = detect_notation_discipline(s)
            self.assertIsNone(
                disc,
                f"False positive triggered on safe sample: '{s}' (flagged as {disc})",
            )
            self.assertFalse(
                is_notation_leak(s),
                f"is_notation_leak returned True on safe sample: '{s}'",
            )

    def test_clean_title_extension_stripping(self):
        self.assertEqual(clean_title("20 - Twinkle Twinkle Little Star.md"), "20 - Twinkle Twinkle Little Star")
        self.assertEqual(clean_title("Cello_Method_Index.pdf"), "Cello Method Index")
        self.assertEqual(clean_title("  My Great Note.markdown  "), "My Great Note")


if __name__ == "__main__":
    unittest.main()
