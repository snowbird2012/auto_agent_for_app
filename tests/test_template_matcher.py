from __future__ import annotations

import unittest

import cv2
import numpy as np

from vision.template_matcher import draw_match, find_subimage


def make_icon(size: int = 52, foreground=(230, 230, 230), background=(28, 36, 48)) -> np.ndarray:
    icon = np.full((size, size, 3), background, dtype=np.uint8)
    cv2.circle(icon, (21, 20), 11, foreground, 4, cv2.LINE_AA)
    cv2.line(icon, (29, 29), (42, 42), foreground, 5, cv2.LINE_AA)
    cv2.circle(icon, (12, 39), 2, (80, 150, 245), -1, cv2.LINE_AA)
    return icon


class TemplateMatcherTest(unittest.TestCase):
    def test_finds_exact_template_center(self) -> None:
        template = make_icon()
        parent = np.full((280, 420, 3), (12, 18, 27), dtype=np.uint8)
        x, y = 247, 126
        parent[y : y + template.shape[0], x : x + template.shape[1]] = template
        result = find_subimage(parent, template, scale_range=(1, 1), scale_steps=1)
        self.assertIsNotNone(result)
        self.assertLessEqual(abs(result.center[0] - (x + 26)), 1)
        self.assertLessEqual(abs(result.center[1] - (y + 26)), 1)

    def test_finds_resized_and_recolored_icon(self) -> None:
        template = make_icon()
        scale = 1.34
        width = round(template.shape[1] * scale)
        height = round(template.shape[0] * scale)
        changed = make_icon(size=52, foreground=(35, 80, 210), background=(225, 220, 205))
        changed = cv2.resize(changed, (width, height), interpolation=cv2.INTER_CUBIC)
        parent = np.full((360, 560, 3), (75, 84, 98), dtype=np.uint8)
        x, y = 333, 176
        parent[y : y + height, x : x + width] = changed
        result = find_subimage(parent, template, threshold=0.55, scale_range=(0.8, 1.6), scale_steps=33)
        self.assertIsNotNone(result)
        self.assertLessEqual(abs(result.center[0] - (x + width // 2)), 3)
        self.assertLessEqual(abs(result.center[1] - (y + height // 2)), 3)
        self.assertAlmostEqual(result.scale, scale, delta=0.08)

    def test_roi_coordinates_are_returned_in_parent_space(self) -> None:
        template = make_icon(40)
        parent = np.zeros((240, 320, 3), dtype=np.uint8)
        parent[140:180, 210:250] = template
        result = find_subimage(parent, template, scale_range=(1, 1), scale_steps=1, roi=(160, 100, 130, 120))
        self.assertEqual(result.top_left, (210, 140))

    def test_returns_none_below_threshold(self) -> None:
        parent = np.zeros((200, 300, 3), dtype=np.uint8)
        result = find_subimage(parent, make_icon(), threshold=0.95, scale_range=(1, 1), scale_steps=1)
        self.assertIsNone(result)

    def test_draw_match_does_not_modify_parent(self) -> None:
        template = make_icon()
        parent = np.zeros((140, 180, 3), dtype=np.uint8)
        parent[40:92, 70:122] = template
        original = parent.copy()
        result = find_subimage(parent, template, scale_range=(1, 1), scale_steps=1)
        annotated = draw_match(parent, result)
        self.assertTrue(np.array_equal(parent, original))
        self.assertFalse(np.array_equal(annotated, original))


if __name__ == "__main__":
    unittest.main()
