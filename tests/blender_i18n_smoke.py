from __future__ import annotations

from pathlib import Path
import sys

import bpy


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import blender_extension
from blender_extension.i18n import tr, trf


blender_extension.register()
view = bpy.context.preferences.view
view.use_translate_interface = True
view.language = "ja_JP"
assert tr("Roblox Sync") == "Roblox同期"
assert tr("Send Selected to Studio") == "選択物をStudioへ送信"
assert trf("Selected {count} objects with the same appearance", count=3) == "同じ外観のオブジェクトを3個選択しました"
print("JAPANESE_TRANSLATION_OK")

view.language = "en_US"
assert tr("Roblox Sync") == "Roblox Sync"
assert tr("Send Selected to Studio") == "Send Selected to Studio"
assert trf("Selected {count} objects with the same appearance", count=3) == "Selected 3 objects with the same appearance"
print("ENGLISH_TRANSLATION_OK")

blender_extension.unregister()
print("I18N_REGISTRATION_OK")
