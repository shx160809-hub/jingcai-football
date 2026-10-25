"""公共工具：配置加载与路径解析。"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

# 项目根目录（本文件在 src/ 下，上级即根目录）
ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config" / "params.yaml"


def load_config() -> dict:
    """加载 config/params.yaml。"""
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def data_dir(name: str) -> Path:
    """返回 data/ 下的子目录，确保存在。"""
    d = ROOT / "data" / name
    d.mkdir(parents=True, exist_ok=True)
    return d
