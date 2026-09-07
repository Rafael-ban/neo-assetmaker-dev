"""M2: 基础专属数据退役后，完整 ConfigPanel 的数据保留回归。"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from tests.qt_harness import ensure_app

from config.epconfig import (
    ArknightsOverlayOptions,
    EditorTrackState,
    EPConfig,
    Overlay,
    OverlayType,
    VSScriptState,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "epconfig"


def setUpModule():
    ensure_app()


class SingleSettingsDataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from gui.widgets.config_panel import ConfigPanel

        cls.panel = ConfigPanel()
        cls.addClassCleanup(cls.panel.deleteLater)

    def _rename_and_serialize(self, config: EPConfig) -> dict:
        before_load = config.to_dict()
        self.panel.set_config(config, "")
        self.assertEqual(config.to_dict(), before_load)
        self.panel.edit_name.setText("仅修改名称")
        return config.to_dict()

    def test_preset_and_custom_class_icons_survive_name_edit(self):
        """共享预设路径和用户自定义路径都不能被完整面板覆盖。"""
        for icon_path in ("class_icons/guard.png", "my_custom_icon.png"):
            with self.subTest(icon_path=icon_path):
                config = EPConfig()
                config.overlay = Overlay(
                    type=OverlayType.ARKNIGHTS,
                    arknights_options=ArknightsOverlayOptions(
                        operator_class_icon=icon_path,
                        aux_text="单行辅助文本",
                    ),
                )

                serialized = self._rename_and_serialize(config)
                options = serialized["overlay"]["options"]
                self.assertEqual(options["operator_class_icon"], icon_path)
                self.assertEqual(options["aux_text"], "单行辅助文本")

    def test_fixture_semantics_survive_name_edit(self):
        """加载 fixture 后仅编辑名称，不得改写任何已解析字段语义。"""
        fixture_paths = sorted(FIXTURE_DIR.glob("*.json"))
        self.assertEqual(len(fixture_paths), 3)
        for path in fixture_paths:
            with self.subTest(fixture=path.name):
                payload = json.loads(path.read_text(encoding="utf-8"))
                expected = EPConfig.from_dict(copy.deepcopy(payload)).to_dict()
                expected["name"] = "仅修改名称"

                actual = self._rename_and_serialize(EPConfig.from_dict(payload))
                self.assertEqual(actual, expected)

    def test_editor_state_survives_name_edit(self):
        """完整面板不拥有 editor 字段，编辑名称不能清空裁剪或脚本状态。"""
        config = EPConfig()
        config.editor.loop = EditorTrackState(
            crop=[1, 2, 300, 500], rotation=90, in_frame=3, out_frame=42
        )
        config.editor.intro = EditorTrackState(
            crop=[4, 5, 200, 350], rotation=270, in_frame=6, out_frame=24
        )
        config.editor.vs_script = VSScriptState(
            source="project", path="vapoursynth/pipeline.vpy"
        )
        expected = config.to_dict()
        expected["name"] = "仅修改名称"

        self.assertEqual(self._rename_and_serialize(config), expected)

    def test_opening_plain_config_after_overlay_does_not_leak_overlay_data(self):
        """连续打开项目后，旧叠加控件不得回写到无叠加的新配置。"""
        rich = EPConfig()
        rich.overlay = Overlay(
            type=OverlayType.ARKNIGHTS,
            arknights_options=ArknightsOverlayOptions(
                operator_name="AMIYA",
                operator_class_icon="class_icons/caster.png",
                aux_text="已存在叠加",
            ),
        )
        self.panel.set_config(rich, "")

        plain = EPConfig()
        serialized = self._rename_and_serialize(plain)
        self.assertNotIn("overlay", serialized)


if __name__ == "__main__":
    unittest.main()
