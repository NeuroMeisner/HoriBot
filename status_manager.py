import json
import os

STATUS_FILE = "status.json"

SYSTEMS_ON_ORDER = ["ИБП", "сетевой фильтр", "лазер 633 нм", "лазер 532 нм", "столик", "детектор", "охлаждение"]
SYSTEMS_OFF_ORDER = list(reversed(SYSTEMS_ON_ORDER))

def _load_status() -> dict:
    if not os.path.exists(STATUS_FILE):
        return {"status": "off", "checked_systems": []}
    try:
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return {"status": "off", "checked_systems": []}

def _save_status(data: dict):
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def get_status() -> str:
    return _load_status().get("status", "off")

def set_status(status: str):
    data = _load_status()
    data["status"] = status
    data["checked_systems"] = []
    _save_status(data)

def get_checked_systems() -> list:
    return _load_status().get("checked_systems", [])

def add_checked_system(system: str):
    data = _load_status()
    if system not in data["checked_systems"]:
        data["checked_systems"].append(system)
    _save_status(data)

def get_systems_order() -> list:
    return SYSTEMS_ON_ORDER if get_status() == "off" else SYSTEMS_OFF_ORDER

def is_all_checked() -> bool:
    order = get_systems_order()
    checked = get_checked_systems()
    return len(checked) >= len(order)