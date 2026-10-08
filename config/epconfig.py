"""
EPConfig 统一数据模型 - 电子通行证素材配置文件
"""
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Literal
from enum import Enum
import uuid as uuid_lib
import json
import os

from core.file_utils import atomic_write_json

CONFIG_FILENAME = "epconfig.json"


class ScreenType(Enum):
    """屏幕分辨率类型"""
    S360x640 = "360x640"
    S720x1080 = "720x1080"

    @classmethod
    def from_string(cls, value: str) -> "ScreenType":
        """从字符串创建枚举"""
        for member in cls:
            if member.value == value:
                return member
        return cls.S360x640


class TransitionType(Enum):
    """过渡效果类型"""
    NONE = "none"
    FADE = "fade"
    MOVE = "move"
    SWIPE = "swipe"

    @classmethod
    def from_string(cls, value: str) -> "TransitionType":
        """从字符串创建枚举"""
        for member in cls:
            if member.value == value:
                return member
        return cls.NONE


class OverlayType(Enum):
    """叠加UI类型"""
    NONE = "none"
    ARKNIGHTS = "arknights"
    IMAGE = "image"

    @classmethod
    def from_string(cls, value: str) -> "OverlayType":
        """从字符串创建枚举"""
        for member in cls:
            if member.value == value:
                return member
        return cls.NONE


@dataclass
class TransitionOptions:
    """过渡效果选项"""
    duration: int = 500000  # 微秒 (0.5秒)
    image: str = ""
    background_color: str = "#000000"

    def to_dict(self, normalize_paths: bool = False) -> dict:
        result = {
            "duration": self.duration,
            "background_color": self.background_color
        }
        if self.image:
            if normalize_paths:
                result["image"] = os.path.basename(self.image)
            else:
                result["image"] = self.image
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "TransitionOptions":
        return cls(
            duration=data.get("duration", 500000),
            image=data.get("image", ""),
            background_color=data.get("background_color", "#000000")
        )


@dataclass
class Transition:
    """过渡效果配置"""
    type: TransitionType = TransitionType.NONE
    options: Optional[TransitionOptions] = None

    def to_dict(self, normalize_paths: bool = False) -> Optional[dict]:
        if self.type == TransitionType.NONE:
            return None
        result = {"type": self.type.value}
        if self.options:
            result["options"] = self.options.to_dict(normalize_paths=normalize_paths)
        return result

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Transition":
        if not data:
            return cls()
        trans_type = TransitionType.from_string(data.get("type", "none"))
        options = None
        if "options" in data:
            options = TransitionOptions.from_dict(data["options"])
        return cls(type=trans_type, options=options)


@dataclass
class LoopConfig:
    """循环动画配置"""
    file: str = ""
    is_image: bool = False  # True=图片模式，False=视频模式

    def to_dict(self, normalize_paths: bool = False) -> dict:
        result = {}
        if normalize_paths and self.file:
            result["file"] = "loop.mp4"
        else:
            result["file"] = self.file
        if self.is_image:
            result["is_image"] = True
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "LoopConfig":
        return cls(
            file=data.get("file", ""),
            is_image=data.get("is_image", False)
        )


@dataclass
class IntroConfig:
    """入场动画配置"""
    enabled: bool = False
    file: str = ""
    duration: int = 5000000  # 微秒 (5秒)

    def to_dict(self, normalize_paths: bool = False) -> Optional[dict]:
        if not self.enabled:
            return None
        return {
            "enabled": True,
            "file": "intro.mp4" if (normalize_paths and self.file) else self.file,
            "duration": self.duration
        }

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "IntroConfig":
        if not data:
            return cls()
        return cls(
            enabled=data.get("enabled", False),
            file=data.get("file", ""),
            duration=data.get("duration", 5000000)
        )


@dataclass
class ArknightsOverlayOptions:
    """明日方舟叠加UI选项"""
    appear_time: int = 100000  # 微秒
    operator_name: str = "OPERATOR"
    top_left_rhodes: str = ""  # 左上角自定义文字，非空时替代默认Rhodes Logo
    top_right_bar_text: str = ""  # 右上角栏自定义文字
    operator_code: str = "ARKNIGHTS - UNK0"
    barcode_text: str = "OPERATOR - ARKNIGHTS"
    aux_text: str = "Operator of Rhodes Island\nUndefined/Rhodes Island\n Hypergryph"
    staff_text: str = "STAFF"
    color: str = "#000000"
    logo: str = ""
    operator_class_icon: str = ""

    def to_dict(self, normalize_paths: bool = False) -> dict:
        result = {
            "appear_time": self.appear_time,
            "operator_name": self.operator_name,
        }
        if self.top_left_rhodes:
            result["top_left_rhodes"] = self.top_left_rhodes
        if self.top_right_bar_text:
            result["top_right_bar_text"] = self.top_right_bar_text
        result["operator_code"] = self.operator_code
        result["barcode_text"] = self.barcode_text
        result["aux_text"] = self.aux_text
        result["staff_text"] = self.staff_text
        result["color"] = self.color
        if self.logo:
            result["logo"] = "ark_logo.png" if normalize_paths else self.logo
        if self.operator_class_icon:
            if self.operator_class_icon.startswith("class_icons/"):
                result["operator_class_icon"] = self.operator_class_icon
            else:
                result["operator_class_icon"] = "class_icon.png" if normalize_paths else self.operator_class_icon
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "ArknightsOverlayOptions":
        return cls(
            appear_time=data.get("appear_time", 100000),
            operator_name=data.get("operator_name", "OPERATOR"),
            top_left_rhodes=data.get("top_left_rhodes", ""),
            top_right_bar_text=data.get("top_right_bar_text", ""),
            operator_code=data.get("operator_code", "ARKNIGHTS - UNK0"),
            barcode_text=data.get("barcode_text", "OPERATOR - ARKNIGHTS"),
            aux_text=data.get("aux_text", "Operator of Rhodes Island\nUndefined/Rhodes Island\n Hypergryph"),
            staff_text=data.get("staff_text", "STAFF"),
            color=data.get("color", "#000000"),
            logo=data.get("logo", ""),
            operator_class_icon=data.get("operator_class_icon", "")
        )


@dataclass
class ImageOverlayOptions:
    """图片叠加UI选项"""
    appear_time: int = 100000  # 微秒
    duration: int = 0  # 微秒 (0 表示无限显示)
    image: str = ""

    def to_dict(self, normalize_paths: bool = False) -> dict:
        result = {
            "appear_time": self.appear_time,
            "duration": self.duration
        }
        if self.image:
            result["image"] = "overlay.png" if normalize_paths else self.image
        return result

    @classmethod
    def from_dict(cls, data: dict) -> "ImageOverlayOptions":
        return cls(
            appear_time=data.get("appear_time", 100000),
            duration=data.get("duration", 0),
            image=data.get("image", "")
        )


@dataclass
class Overlay:
    """叠加UI配置"""
    type: OverlayType = OverlayType.NONE
    arknights_options: Optional[ArknightsOverlayOptions] = None
    image_options: Optional[ImageOverlayOptions] = None

    def to_dict(self, normalize_paths: bool = False) -> Optional[dict]:
        if self.type == OverlayType.NONE:
            return None
        result = {"type": self.type.value}
        if self.type == OverlayType.ARKNIGHTS and self.arknights_options:
            result["options"] = self.arknights_options.to_dict(normalize_paths=normalize_paths)
        elif self.type == OverlayType.IMAGE and self.image_options:
            result["options"] = self.image_options.to_dict(normalize_paths=normalize_paths)
        return result

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "Overlay":
        if not data:
            return cls()
        overlay_type = OverlayType.from_string(data.get("type", "none"))
        arknights_opts = None
        image_opts = None
        if overlay_type == OverlayType.ARKNIGHTS and "options" in data:
            arknights_opts = ArknightsOverlayOptions.from_dict(data["options"])
        elif overlay_type == OverlayType.IMAGE and "options" in data:
            image_opts = ImageOverlayOptions.from_dict(data["options"])
        return cls(
            type=overlay_type,
            arknights_options=arknights_opts,
            image_options=image_opts
        )


@dataclass
class EditorTrackState:
    """单轨(循环/入场)的编辑器状态。

    仅存在于项目文件,导出包的 epconfig.json 会剥离(见 EPConfig.to_dict)。
    crop 为旋转后空间的像素 [x, y, w, h];in/out 为帧索引(-1 = 未设置)。
    """
    crop: Optional[List[int]] = None
    rotation: int = 0
    in_frame: int = -1
    out_frame: int = -1

    def is_default(self) -> bool:
        return (
            self.crop is None
            and self.rotation == 0
            and self.in_frame < 0
            and self.out_frame < 0
        )

    def to_dict(self) -> dict:
        result: Dict[str, Any] = {}
        if self.crop is not None:
            result["crop"] = [int(v) for v in self.crop]
        if self.rotation:
            result["rotation"] = int(self.rotation)
        if self.in_frame >= 0:
            result["in_frame"] = int(self.in_frame)
        if self.out_frame >= 0:
            result["out_frame"] = int(self.out_frame)
        return result

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "EditorTrackState":
        if not isinstance(data, dict):
            return cls()
        crop = data.get("crop")
        if isinstance(crop, (list, tuple)) and len(crop) == 4:
            crop = [int(v) for v in crop]
        else:
            crop = None
        return cls(
            crop=crop,
            rotation=int(data.get("rotation", 0) or 0),
            in_frame=int(data.get("in_frame", -1)),
            out_frame=int(data.get("out_frame", -1)),
        )


@dataclass
class VSScriptState:
    """项目文件中仅保存的 VPY 来源；运行身份均从脚本 header 派生。"""

    source: Literal["builtin", "global", "project"] = "builtin"
    path: str = ""

    def is_default(self) -> bool:
        return self.source == "builtin" and not self.path

    def to_dict(self) -> dict:
        return {"source": self.source, "path": self.path}

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "VSScriptState":
        if not isinstance(data, dict):
            return cls()
        source = data.get("source", "builtin")
        path = data.get("path", "")
        if source not in ("builtin", "global", "project") or not isinstance(path, str):
            return cls()
        if source in ("builtin", "global"):
            return cls(source=source, path="")
        if (
            not path
            or "\\" in path
            or os.path.isabs(path)
            or any(part in ("", ".", "..") for part in path.split("/"))
        ):
            return cls()
        return cls(source="project", path=path)


@dataclass
class EditorState:
    """编辑器状态(裁剪框/旋转/入出点),挂在项目 epconfig.json 的 editor 键下。"""
    loop: EditorTrackState = field(default_factory=EditorTrackState)
    intro: EditorTrackState = field(default_factory=EditorTrackState)
    vs_script: VSScriptState = field(default_factory=VSScriptState)

    def is_default(self) -> bool:
        return (
            self.loop.is_default()
            and self.intro.is_default()
            and self.vs_script.is_default()
        )

    def to_dict(self) -> dict:
        result: Dict[str, Any] = {}
        if not self.loop.is_default():
            result["loop"] = self.loop.to_dict()
        if not self.intro.is_default():
            result["intro"] = self.intro.to_dict()
        if not self.vs_script.is_default():
            result["vs_script"] = self.vs_script.to_dict()
        return result

    @classmethod
    def from_dict(cls, data: Optional[dict]) -> "EditorState":
        if not isinstance(data, dict):
            return cls()
        return cls(
            loop=EditorTrackState.from_dict(data.get("loop")),
            intro=EditorTrackState.from_dict(data.get("intro")),
            vs_script=VSScriptState.from_dict(data.get("vs_script")),
        )


@dataclass
class EPConfig:
    """epconfig.json 完整数据模型"""
    version: int = 1
    uuid: str = field(default_factory=lambda: str(uuid_lib.uuid4()))
    name: str = ""
    description: str = ""
    icon: str = ""
    screen: ScreenType = ScreenType.S360x640
    loop: LoopConfig = field(default_factory=LoopConfig)
    intro: IntroConfig = field(default_factory=IntroConfig)
    transition_in: Transition = field(default_factory=Transition)
    transition_loop: Transition = field(default_factory=Transition)
    overlay: Overlay = field(default_factory=Overlay)
    editor: EditorState = field(default_factory=EditorState)

    def to_dict(self, normalize_paths: bool = False) -> dict:
        """转换为可序列化的字典

        Args:
            normalize_paths: 为 True 时将文件路径替换为标准化的导出文件名
                           （如 loop.mp4, icon.png, overlay.png 等）
        """
        result = {
            "version": self.version,
            "uuid": self.uuid,
            "screen": self.screen.value,
            "loop": self.loop.to_dict(normalize_paths=normalize_paths)
        }

        if self.name:
            result["name"] = self.name
        if self.description:
            result["description"] = self.description
        if self.icon:
            result["icon"] = "icon.png" if normalize_paths else self.icon

        intro_dict = self.intro.to_dict(normalize_paths=normalize_paths)
        if intro_dict:
            result["intro"] = intro_dict

        trans_in_dict = self.transition_in.to_dict(normalize_paths=normalize_paths)
        if trans_in_dict:
            if normalize_paths and trans_in_dict.get("options", {}).get("image"):
                trans_in_dict["options"]["image"] = "transition_in.png"
            result["transition_in"] = trans_in_dict

        trans_loop_dict = self.transition_loop.to_dict(normalize_paths=normalize_paths)
        if trans_loop_dict:
            if normalize_paths and trans_loop_dict.get("options", {}).get("image"):
                trans_loop_dict["options"]["image"] = "transition_loop.png"
            result["transition_loop"] = trans_loop_dict

        overlay_dict = self.overlay.to_dict(normalize_paths=normalize_paths)
        if overlay_dict:
            result["overlay"] = overlay_dict

        # editor 状态只进项目文件(normalize_paths=False = save_to_file 路径),
        # 导出包(normalize_paths=True)剥离:包内 epconfig.json 是设备契约
        # (schemas/epconfig.schema.json),设备固件解析器无法验证其对未知键的
        # 容忍度,故不携带任何编辑器私有数据。
        if not normalize_paths and not self.editor.is_default():
            result["editor"] = self.editor.to_dict()

        return result

    def to_json(self, indent: int = 4, normalize_paths: bool = False) -> str:
        """转换为JSON字符串"""
        return json.dumps(self.to_dict(normalize_paths=normalize_paths), ensure_ascii=False, indent=indent)

    @staticmethod
    def _detect_invalid_enums(data: dict) -> list:
        """Return [(field, raw_value)] for any enum value that from_string would
        silently coerce to a default. Lets the validator report a corrupt config
        instead of silently exporting at e.g. the wrong resolution."""
        invalid: list = []
        screen_vals = {m.value for m in ScreenType}
        trans_vals = {m.value for m in TransitionType}
        overlay_vals = {m.value for m in OverlayType}

        screen_raw = data.get("screen")
        if isinstance(screen_raw, str) and screen_raw and screen_raw not in screen_vals:
            invalid.append(("screen", screen_raw))
        for key in ("transition_in", "transition_loop"):
            section = data.get(key)
            t_raw = section.get("type") if isinstance(section, dict) else None
            if isinstance(t_raw, str) and t_raw and t_raw not in trans_vals:
                invalid.append((f"{key}.type", t_raw))
        overlay = data.get("overlay")
        ov_raw = overlay.get("type") if isinstance(overlay, dict) else None
        if isinstance(ov_raw, str) and ov_raw and ov_raw not in overlay_vals:
            invalid.append(("overlay.type", ov_raw))
        return invalid

    @classmethod
    def from_dict(cls, data: dict) -> "EPConfig":
        """从字典创建实例"""
        screen = ScreenType.from_string(data.get("screen", "360x640"))

        config = cls(
            version=data.get("version", 1),
            uuid=data.get("uuid", str(uuid_lib.uuid4())),
            name=data.get("name", ""),
            description=data.get("description", ""),
            icon=data.get("icon", ""),
            screen=screen,
            loop=LoopConfig.from_dict(data.get("loop", {})),
            intro=IntroConfig.from_dict(data.get("intro")),
            transition_in=Transition.from_dict(data.get("transition_in")),
            transition_loop=Transition.from_dict(data.get("transition_loop")),
            overlay=Overlay.from_dict(data.get("overlay")),
            editor=EditorState.from_dict(data.get("editor")),
        )
        # from_string above stays lenient (defaults) so the app keeps working; record the
        # raw invalid values here so validate_config can surface them as errors.
        config._invalid_fields = cls._detect_invalid_enums(data)
        return config

    @classmethod
    def load_from_file(cls, filepath: str) -> "EPConfig":
        """从文件加载配置"""
        with open(filepath, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return cls.from_dict(data)

    def save_to_file(self, filepath: str):
        """保存配置到文件"""
        try:
            directory = os.path.dirname(filepath)
            if directory and not os.path.exists(directory):
                os.makedirs(directory)

            atomic_write_json(filepath, self.to_dict(), indent=4)
        except PermissionError:
            raise RuntimeError(f"无法保存到 {filepath}，权限不足")

    def generate_new_uuid(self):
        """生成新的UUID"""
        self.uuid = str(uuid_lib.uuid4())

    def copy(self) -> "EPConfig":
        """创建配置的深拷贝"""
        return EPConfig.from_dict(self.to_dict())
