"""Resolution and color tolerant sub-image matching based on OpenCV."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

import cv2
import numpy as np


ImageInput: TypeAlias = str | Path | np.ndarray


@dataclass(frozen=True, slots=True)
class MatchResult:
    """A template match expressed in the parent image coordinate system."""

    top_left: tuple[int, int]
    bottom_right: tuple[int, int]
    center: tuple[int, int]
    size: tuple[int, int]
    confidence: float
    scale: float
    scale_x: float
    scale_y: float

    @property
    def x(self) -> int:
        """Center x coordinate, useful for an ADB tap."""
        return self.center[0]

    @property
    def y(self) -> int:
        """Center y coordinate, useful for an ADB tap."""
        return self.center[1]


def find_subimage(
    parent_image: ImageInput,
    template_image: ImageInput,
    *,
    threshold: float = 0.70,
    scale_range: tuple[float, float] = (0.55, 1.80),
    scale_steps: int = 27,
    aspect_tolerance: float = 0.0,
    roi: tuple[int, int, int, int] | None = None,
) -> MatchResult | None:
    """Find a template inside a screenshot despite moderate size/color changes.

    Args:
        parent_image: Screenshot as a path or OpenCV/numpy image.
        template_image: Manually cropped template as a path or numpy image.
        threshold: Minimum hybrid confidence in the [0, 1] interval.
        scale_range: Smallest and largest template scale to search.
        scale_steps: Number of scales between the two boundaries.
        aspect_tolerance: Optional x/y distortion range. 0.08 searches -8%, 0,
            and +8% width variants at every scale. Leave at zero for speed.
        roi: Optional parent search region as (x, y, width, height).

    Returns:
        The best match with center/bounds/confidence, or None when the best
        candidate is below ``threshold``.

    The confidence combines locally normalized grayscale information and Canny
    edges. Edge matching makes the result tolerant of tint, brightness, dark
    mode and moderate compression changes; the scale pyramid handles different
    phone resolution/density settings.
    """
    if not 0 <= threshold <= 1:
        raise ValueError("threshold 必须在 0 到 1 之间")
    min_scale, max_scale = scale_range
    if min_scale <= 0 or max_scale < min_scale:
        raise ValueError("scale_range 必须是两个有效的正数，且最大值不小于最小值")
    if scale_steps < 1:
        raise ValueError("scale_steps 至少为 1")
    if not 0 <= aspect_tolerance <= 0.5:
        raise ValueError("aspect_tolerance 必须在 0 到 0.5 之间")

    parent = _load_image(parent_image, "父图")
    template = _load_image(template_image, "子图")
    search, offset = _crop_roi(parent, roi)
    parent_gray = _normalize_gray(search)
    parent_edges = _edges(parent_gray)

    scales = np.linspace(min_scale, max_scale, scale_steps, dtype=np.float32)
    aspect_factors = (1.0,) if aspect_tolerance == 0 else (1.0 - aspect_tolerance, 1.0, 1.0 + aspect_tolerance)
    best_score = -1.0
    best_location = (0, 0)
    best_size = (0, 0)
    best_scales = (1.0, 1.0)
    original_height, original_width = template.shape[:2]
    search_height, search_width = search.shape[:2]

    for scale in scales:
        for aspect in aspect_factors:
            scale_x = float(scale * aspect)
            scale_y = float(scale)
            width = max(2, int(round(original_width * scale_x)))
            height = max(2, int(round(original_height * scale_y)))
            if width > search_width or height > search_height:
                continue
            interpolation = cv2.INTER_AREA if scale_x < 1 or scale_y < 1 else cv2.INTER_CUBIC
            resized = cv2.resize(template, (width, height), interpolation=interpolation)
            template_gray = _normalize_gray(resized)
            template_edges = _edges(template_gray)

            gray_scores = cv2.matchTemplate(parent_gray, template_gray, cv2.TM_CCOEFF_NORMED)
            # Canny may be empty for a nearly flat template. In that case the
            # grayscale result is the only meaningful signal.
            if np.count_nonzero(template_edges) >= 8:
                edge_scores = cv2.matchTemplate(parent_edges, template_edges, cv2.TM_CCORR_NORMED)
                positive_gray = np.maximum(gray_scores, 0)
                balanced = positive_gray * 0.38 + edge_scores * 0.62
                # A light/dark theme switch can invert grayscale correlation
                # while preserving the icon outline. Keep an edge-dominant
                # route so that this valid case is not artificially capped.
                edge_dominant = positive_gray * 0.08 + edge_scores * 0.92
                combined = np.maximum(balanced, edge_dominant)
            else:
                combined = np.maximum(gray_scores, 0)
            _, score, _, location = cv2.minMaxLoc(combined)
            if score > best_score:
                best_score = float(score)
                best_location = location
                best_size = (width, height)
                best_scales = (scale_x, scale_y)

    if best_score < threshold or best_size == (0, 0):
        return None
    left = best_location[0] + offset[0]
    top = best_location[1] + offset[1]
    width, height = best_size
    return MatchResult(
        top_left=(left, top),
        bottom_right=(left + width, top + height),
        center=(left + width // 2, top + height // 2),
        size=(width, height),
        confidence=best_score,
        scale=(best_scales[0] + best_scales[1]) / 2,
        scale_x=best_scales[0],
        scale_y=best_scales[1],
    )


def draw_match(
    parent_image: ImageInput,
    result: MatchResult,
    *,
    color: tuple[int, int, int] = (45, 220, 120),
    thickness: int = 2,
) -> np.ndarray:
    """Return a copy of the parent image annotated with a match rectangle."""
    image = _load_image(parent_image, "父图").copy()
    cv2.rectangle(image, result.top_left, result.bottom_right, color, thickness)
    cv2.drawMarker(image, result.center, color, cv2.MARKER_CROSS, 18, thickness)
    caption = f"{result.confidence:.3f}  scale={result.scale:.2f}"
    text_y = max(18, result.top_left[1] - 7)
    cv2.putText(image, caption, (result.top_left[0], text_y), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    return image


def _load_image(value: ImageInput, name: str) -> np.ndarray:
    if isinstance(value, (str, Path)):
        # imdecode supports paths containing Chinese characters on Windows.
        try:
            data = np.fromfile(str(value), dtype=np.uint8)
            image = cv2.imdecode(data, cv2.IMREAD_COLOR)
        except OSError as error:
            raise ValueError(f"无法读取{name}：{value}") from error
    elif isinstance(value, np.ndarray):
        image = value.copy()
    else:
        raise TypeError(f"{name}必须是文件路径或 numpy.ndarray")
    if image is None or image.size == 0:
        raise ValueError(f"{name}为空或不是有效图像")
    if image.ndim == 2:
        return cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
    if image.ndim != 3 or image.shape[2] not in (3, 4):
        raise ValueError(f"{name}必须是灰度、BGR 或 BGRA 图像")
    if image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
    return image


def _normalize_gray(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    # CLAHE is more stable than global histogram equalization when the phone
    # screenshot changes brightness or contains a gradient background.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _edges(gray: np.ndarray) -> np.ndarray:
    blurred = cv2.GaussianBlur(gray, (3, 3), 0)
    median = float(np.median(blurred))
    lower = int(max(20, 0.60 * median))
    upper = int(min(255, max(lower + 20, 1.40 * median)))
    return cv2.Canny(blurred, lower, upper)


def _crop_roi(parent: np.ndarray, roi: tuple[int, int, int, int] | None) -> tuple[np.ndarray, tuple[int, int]]:
    if roi is None:
        return parent, (0, 0)
    x, y, width, height = roi
    if x < 0 or y < 0 or width <= 0 or height <= 0 or x + width > parent.shape[1] or y + height > parent.shape[0]:
        raise ValueError("roi 超出父图范围或尺寸无效")
    return parent[y : y + height, x : x + width], (x, y)
