"""
图片预览组件 - 支持Logo、Overlay、Display三种预览模式
"""

import cv2
import numpy as np
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QImage, QPixmap

SCREEN_WIDTH, SCREEN_HEIGHT = 360, 640
LOGO_WIDTH, LOGO_HEIGHT = 256, 256


class ImagePreviewWidget(QWidget):
    """图片预览组件"""

    image_loaded = pyqtSignal(int, int, bool)
    preview_mode_changed = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        self._original_image = None
        self._processed_image = None
        self._preview_mode = 'logo'
        self._background_color = 0xFFFFFFFF
        self._chessboard_cache = {}

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)

        self._info_label = QLabel("模式: Logo | 未加载图片")
        self._info_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._info_label.setStyleSheet("background-color: #333; color: #fff; padding: 5px;")
        layout.addWidget(self._info_label)

        self._image_label = QLabel()
        self._image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._image_label.setMinimumSize(SCREEN_WIDTH, SCREEN_HEIGHT)
        self._image_label.setStyleSheet("background-color: #1a1a1a; border: 1px solid #444;")
        layout.addWidget(self._image_label, 1)

    def _make_chessboard_img(self, width: int, height: int) -> np.ndarray:
        key = (width, height)
        if key in self._chessboard_cache:
            return self._chessboard_cache[key].copy()

        img = np.zeros((height, width, 4), dtype=np.float64)
        for y in range(height):
            for x in range(width):
                if (x // 10 % 2) ^ (y // 10 % 2) == 0:
                    img[y, x] = [127, 127, 127, 255]
                else:
                    img[y, x] = [0, 0, 0, 255]

        self._chessboard_cache[key] = img.copy()
        return img

    def _parse_argb_color(self, argb: int) -> tuple:
        a = (argb >> 24) & 0xFF
        r = (argb >> 16) & 0xFF
        g = (argb >> 8) & 0xFF
        b = argb & 0xFF
        return a, r, g, b

    def _process_logo(self, image: np.ndarray) -> np.ndarray:
        img_h, img_w = image.shape[:2]
        scale = min(LOGO_WIDTH / img_w, LOGO_HEIGHT / img_h)
        new_w, new_h = int(img_w * scale), int(img_h * scale)

        logo = cv2.resize(image, (new_w, new_h), interpolation=cv2.INTER_CUBIC)
        if len(logo.shape) == 2:
            logo = cv2.cvtColor(logo, cv2.COLOR_GRAY2BGRA)
        elif logo.shape[2] == 3:
            logo = cv2.cvtColor(logo, cv2.COLOR_BGR2BGRA)

        logo_full = np.zeros((LOGO_HEIGHT, LOGO_WIDTH, 4), dtype=np.uint8)
        sx, sy = (LOGO_WIDTH - new_w) // 2, (LOGO_HEIGHT - new_h) // 2
        logo_full[sy:sy+new_h, sx:sx+new_w] = logo

        bg_a, bg_r, bg_g, bg_b = self._parse_argb_color(self._background_color)
        result = np.zeros((SCREEN_HEIGHT, SCREEN_WIDTH, 4), dtype=np.uint8)
        result[:, :] = [bg_b, bg_g, bg_r, bg_a]

        lx = SCREEN_WIDTH // 2 - LOGO_WIDTH // 2
        ly = SCREEN_HEIGHT // 2 - LOGO_HEIGHT // 2
        result[ly:ly+LOGO_HEIGHT, lx:lx+LOGO_WIDTH] = logo_full

        return result

    def _process_overlay_or_display(self, image: np.ndarray, tw: int, th: int) -> np.ndarray:
        img_h, img_w = image.shape[:2]
        scale = tw / img_w
        rw, rh = tw, int(img_h * scale)

        resized = cv2.resize(image, (rw, rh), interpolation=cv2.INTER_CUBIC)
        if len(resized.shape) == 2:
            resized = cv2.cvtColor(resized, cv2.COLOR_GRAY2BGRA)
        elif resized.shape[2] == 3:
            resized = cv2.cvtColor(resized, cv2.COLOR_BGR2BGRA)

        resized = resized[:th, :tw, :]

        if resized.shape[0] < th:
            padding = np.zeros((th - resized.shape[0], tw, 4), dtype=resized.dtype)
            resized = np.vstack([padding, resized])
        if resized.shape[1] < tw:
            padding = np.zeros((th, tw - resized.shape[1], 4), dtype=resized.dtype)
            resized = np.hstack([resized, padding])

        return resized.astype(np.uint8)

    def _blend_with_chessboard(self, image: np.ndarray) -> np.ndarray:
        h, w = image.shape[:2]
        bg = self._make_chessboard_img(w, h)
        alpha = image[..., 3:4].astype(np.float64) / 255.0
        blended_rgb = image[..., :3].astype(np.float64) * alpha + bg[..., :3] * (1 - alpha)
        blended = np.concatenate([blended_rgb, np.full_like(alpha, 255)], axis=2)
        return blended.astype(np.uint8)

    def _update_preview(self):
        if self._original_image is None:
            self._image_label.clear()
            self._update_info_label()
            return

        if self._preview_mode == 'logo':
            self._processed_image = self._process_logo(self._original_image)
            preview = self._processed_image.copy()
        else:
            self._processed_image = self._process_overlay_or_display(
                self._original_image, SCREEN_WIDTH, SCREEN_HEIGHT
            )
            preview = self._blend_with_chessboard(self._processed_image)

        self._display_image(preview)
        self._update_info_label()

    def _display_image(self, image: np.ndarray):
        rgb_image = cv2.cvtColor(image, cv2.COLOR_BGRA2RGBA)
        h, w, ch = rgb_image.shape
        qt_image = QImage(rgb_image.data, w, h, ch * w, QImage.Format.Format_RGBA8888)
        pixmap = QPixmap.fromImage(qt_image)
        scaled = pixmap.scaled(self._image_label.size(), Qt.AspectRatioMode.KeepAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
        self._image_label.setPixmap(scaled)

    def _update_info_label(self):
        modes = {'logo': 'Logo (256x256)', 'overlay': 'Overlay (360x640)', 'display': 'Display (360x640)'}
        mode_name = modes.get(self._preview_mode, self._preview_mode)

        if self._original_image is None:
            self._info_label.setText(f"模式: {mode_name} | 未加载图片")
        else:
            h, w = self._original_image.shape[:2]
            has_alpha = len(self._original_image.shape) == 3 and self._original_image.shape[2] == 4
            self._info_label.setText(f"模式: {mode_name} | {w}x{h} | {'有' if has_alpha else '无'}Alpha")

    def load_image(self, path: str) -> bool:
        try:
            image = cv2.imread(path, cv2.IMREAD_UNCHANGED)
            if image is None:
                return False

            if len(image.shape) == 2:
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGRA)
            elif image.shape[2] == 3:
                image = cv2.cvtColor(image, cv2.COLOR_BGR2BGRA)

            self._original_image = image
            h, w = image.shape[:2]
            has_alpha = image.shape[2] == 4
            self.image_loaded.emit(w, h, has_alpha)
            self._update_preview()
            return True
        except Exception:
            return False

    def set_preview_mode(self, mode: str):
        if mode in ('logo', 'overlay', 'display') and mode != self._preview_mode:
            self._preview_mode = mode
            self.preview_mode_changed.emit(mode)
            self._update_preview()

    def set_background_color(self, argb_int: int):
        self._background_color = argb_int
        if self._preview_mode == 'logo':
            self._update_preview()

    def get_processed_image(self) -> np.ndarray:
        return self._processed_image.copy() if self._processed_image is not None else None

    def clear(self):
        self._original_image = None
        self._processed_image = None
        self._image_label.clear()
        self._update_info_label()
