# -*- coding: utf-8 -*-
"""config 模块单元测试：配置读写、历史追加、批次弹出。"""
import os

import pytest

import config as cfg


def test_load_config_defaults_when_missing(tmp_path, monkeypatch):
    """配置文件不存在时应返回默认配置。"""
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "h.json"))
    data = cfg.load_config()
    assert data["storageRoot"] == cfg.DEFAULT_CONFIG["storageRoot"]
    assert isinstance(data["categories"], list)


def test_load_config_corrupt_file_returns_default(tmp_path, monkeypatch):
    """配置文件损坏时应静默退回默认配置。"""
    bad = tmp_path / "s.json"
    bad.write_text("{not valid json", encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(bad))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "h.json"))
    assert cfg.load_config() == cfg.DEFAULT_CONFIG


def test_load_config_recovers_from_backup(tmp_path, monkeypatch):
    """主配置损坏而 .bak 完好时，应从备份恢复而不是丢掉全部设置。"""
    main = tmp_path / "s.json"
    main.write_text("{broken", encoding="utf-8")
    (tmp_path / "s.json.bak").write_text(
        '{"widgetPositions": {}, "storageRoot": "E:/backup-root",'
        ' "categories": ["文档"]}', encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(main))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "h.json"))

    data = cfg.load_config()

    assert data["storageRoot"] == "E:/backup-root"
    assert data["categories"] == ["文档"]


def test_load_config_sanitizes_bad_types(tmp_path, monkeypatch):
    """被手改坏的字段只回退该字段，不能让错误类型进入运行期。"""
    path = tmp_path / "s.json"
    path.write_text(
        '{"storageRoot": 123, "categories": "文档", "includeFolders": "yes",'
        ' "autoOrganizeDelay": -9, "widgetSizes": [1, 2],'
        ' "widgetPositions": {"文档": "1,2"}}', encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(path))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "h.json"))

    data = cfg.load_config()

    assert data["storageRoot"] == cfg.DEFAULT_CONFIG["storageRoot"]
    assert isinstance(data["categories"], list)
    assert data["includeFolders"] is cfg.DEFAULT_CONFIG["includeFolders"]
    assert data["autoOrganizeDelay"] == 0
    assert data["widgetSizes"] == {}
    assert data["widgetPositions"] == {"文档": "1,2"}


def test_save_config_keeps_previous_backup(tmp_path, monkeypatch):
    """第二次保存时旧配置应轮换为 .bak，供损坏时恢复。"""
    path = tmp_path / "s.json"
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(path))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "h.json"))
    cfg.save_config(dict(cfg.DEFAULT_CONFIG, storageRoot="E:/one"))
    cfg.save_config(dict(cfg.DEFAULT_CONFIG, storageRoot="E:/two"))

    import json
    backup = json.loads((tmp_path / "s.json.bak").read_text(encoding="utf-8"))
    assert backup["storageRoot"] == "E:/one"
    assert json.loads(path.read_text(encoding="utf-8"))["storageRoot"] == "E:/two"


def test_load_history_recovers_from_backup(tmp_path, monkeypatch):
    """历史主文件损坏时从 .bak 读取，撤销记录不至于全丢。"""
    main = tmp_path / "h.json"
    main.write_text("[broken", encoding="utf-8")
    (tmp_path / "h.json.bak").write_text(
        '[{"src": "a", "dst": "b", "batch": "b1"}]', encoding="utf-8")
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(main))
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "s.json"))

    assert cfg.load_history() == [{"src": "a", "dst": "b", "batch": "b1"}]



def test_config_roundtrip(tmp_path, monkeypatch):
    """保存后读取应得到相同内容。"""
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "h.json"))
    data = dict(cfg.DEFAULT_CONFIG, storageRoot="D:/new/root")
    cfg.save_config(data)
    assert cfg.load_config()["storageRoot"] == "D:/new/root"


def test_history_append_and_pop_batch(tmp_path, monkeypatch):
    """追加多条记录后应能整批弹出最近批次。"""
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "h.json"))
    cfg.append_records([{"src": "a", "dst": "b", "batch": "b1"}])
    cfg.append_records([{"src": "c", "dst": "d", "batch": "b2"}])

    popped = cfg.pop_last_batch()
    assert len(popped) == 1 and popped[0]["batch"] == "b2"
    remaining = cfg.load_history()
    assert len(remaining) == 1 and remaining[0]["batch"] == "b1"


def test_pop_batch_empty_history(tmp_path, monkeypatch):
    """历史为空时 pop 应返回空列表且不报错。"""
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "h.json"))
    assert cfg.pop_last_batch() == []


def test_pop_last_batch_without_batch_falls_back_to_single(tmp_path, monkeypatch):
    """历史末条没有 batch（旧数据）时应单条弹出，保证"撤销上次"始终可用。"""
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "h.json"))
    cfg.append_records([{"src": "a", "dst": "b", "batch": "b1"}])
    cfg.append_records([{"src": "c", "dst": "d"}])          # 无 batch 的旧记录

    popped = cfg.pop_last_batch()

    assert len(popped) == 1 and popped[0]["src"] == "c"
    assert [r["src"] for r in cfg.load_history()] == ["a"]


def test_history_cap(tmp_path, monkeypatch):
    """历史超过上限时应丢弃最旧记录。"""
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "h.json"))
    monkeypatch.setattr(cfg, "HISTORY_MAX_RECORDS", 3)
    for i in range(5):
        cfg.append_records([{"src": str(i), "dst": str(i), "batch": f"b{i}"}])
    history = cfg.load_history()
    assert len(history) == 3
    assert history[0]["batch"] == "b2"  # 最旧两条已被丢弃


# ---------- v2 新配置键 ----------

def test_defaults_contain_v2_keys():
    """默认配置应包含 v2 新增键。"""
    for key in ("includeFolders", "autostart", "previewIcons",
                "gameMode", "widgetPositions", "widgetSizes",
                "confirmOrganize", "panelGeometry", "widgetScales",
                "widgetPreviews", "gridPaths", "autoOrganize",
                "autoOrganizeDelay", "searchGrid", "widgetCollapsed",
                "shellMenu", "updateUrl"):
        assert key in cfg.DEFAULT_CONFIG
    assert cfg.FOLDER_CATEGORY in cfg.DEFAULT_CONFIG["categories"]


def test_legacy_config_gets_folder_category(tmp_path, monkeypatch):
    """v1 旧配置（无 widgetPositions）加载时应自动补"文件夹"分类。"""
    old_cfg = tmp_path / "s.json"
    old_cfg.write_text(
        '{"storageRoot": "E:/old", "includeShortcuts": false,'
        ' "categories": ["文档", "图片"]}', encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(old_cfg))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "h.json"))

    data = cfg.load_config()

    assert data["storageRoot"] == "E:/old"          # 旧值保留
    assert cfg.FOLDER_CATEGORY in data["categories"]  # 自动补文件夹分类


def test_v2_config_not_renormalized(tmp_path, monkeypatch):
    """v2 配置（有 widgetPositions）加载时不应强制补分类，尊重用户选择。"""
    user_cfg = tmp_path / "s.json"
    user_cfg.write_text(
        '{"widgetPositions": {}, "categories": ["文档"]}', encoding="utf-8")
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(user_cfg))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "h.json"))

    assert cfg.load_config()["categories"] == ["文档"]


def test_position_format_parse_roundtrip():
    """位置坐标序列化/解析应可互逆。"""
    text = cfg.format_position(123, -45)
    assert text == "123,-45"
    assert cfg.parse_position(text) == (123, -45)


def test_parse_position_invalid_returns_none():
    """非法坐标字符串应返回 None 而不是抛异常。"""
    assert cfg.parse_position("") is None
    assert cfg.parse_position("abc,def") is None
    assert cfg.parse_position("1,") is None
    assert cfg.parse_position(None) is None


# ---------- 格子名称 / 几何 / 历史路径工具 ----------

def test_validate_grid_name():
    """名称校验：合法通过，空/非法字符/重名/句点结尾应给出错误提示。"""
    existing = ["文档", "课程资料"]
    assert cfg.validate_grid_name("期末复习", existing) == ""
    assert "不能为空" in cfg.validate_grid_name("   ", existing)
    assert "已存在" in cfg.validate_grid_name("课程资料", existing)
    assert "不能包含" in cfg.validate_grid_name("a/b", existing)
    assert "句点" in cfg.validate_grid_name("资料.", existing)


def test_geometry_roundtrip_and_invalid():
    """窗口几何序列化/解析可互逆，非法输入返回 None。"""
    text = cfg.format_geometry(10, 20, 480, 660)
    assert text == "10,20,480,660"
    assert cfg.parse_geometry(text) == (10, 20, 480, 660)
    assert cfg.parse_geometry("1,2,3") is None
    assert cfg.parse_geometry("a,b,c,d") is None
    assert cfg.parse_geometry("") is None


def test_retarget_history_prefix(tmp_path, monkeypatch):
    """重命名格子后，历史记录中旧目录下的 dst 应改到新目录（含子目录）。"""
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "h.json"))
    old_dir = str(tmp_path / "旧格子")
    new_dir = str(tmp_path / "新格子")
    cfg.append_records([
        {"src": "D:/a.pdf", "dst": os.path.join(old_dir, "a.pdf"), "batch": "b1"},
        {"src": "D:/b.pdf", "dst": os.path.join(old_dir, "子目录", "b.pdf"),
         "batch": "b1"},
        {"src": "D:/c.pdf", "dst": "E:/elsewhere/c.pdf", "batch": "b2"},
    ])

    changed = cfg.retarget_history_prefix(old_dir, new_dir)

    assert changed == 2
    history = cfg.load_history()
    assert history[0]["dst"] == os.path.join(new_dir, "a.pdf")
    assert history[1]["dst"] == os.path.join(new_dir, "子目录", "b.pdf")
    assert history[2]["dst"] == "E:/elsewhere/c.pdf"


# ---------- 原子写入（防崩溃损坏） ----------

def test_save_config_atomic_keeps_old_on_write_error(tmp_path, monkeypatch):
    """写入中断（os.replace 失败）时旧配置保持完整，且不残留 .tmp 文件。"""
    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "h.json"))
    cfg.save_config(dict(cfg.DEFAULT_CONFIG, storageRoot="E:/旧的"))
    old_text = (tmp_path / "s.json").read_text(encoding="utf-8")

    def interrupted(src, dst):
        raise OSError("模拟崩溃/断电")

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError):
        cfg.save_config(dict(cfg.DEFAULT_CONFIG, storageRoot="D:/新的"))
    # 注意：不在用例内调用 monkeypatch.undo()，它会连 CONFIG_PATH 的补丁
    # 一起撤销，导致后续断言写到真实用户目录去。

    assert (tmp_path / "s.json").read_text(encoding="utf-8") == old_text
    assert [p.name for p in tmp_path.iterdir()] == ["s.json"]   # 无 .tmp 残留


def test_save_history_atomic(tmp_path, monkeypatch):
    """历史文件原子写入：写入中断时不破坏旧历史，也不会残留 .tmp。"""
    import json

    monkeypatch.setattr(cfg, "CONFIG_PATH", str(tmp_path / "s.json"))
    monkeypatch.setattr(cfg, "HISTORY_PATH", str(tmp_path / "h.json"))
    cfg.append_records([{"src": "a", "dst": "b", "batch": "b1"}])
    old_text = (tmp_path / "h.json").read_text(encoding="utf-8")

    def interrupted(src, dst):
        raise OSError("模拟磁盘满")

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError):
        cfg.save_history([{"src": "c", "dst": "d", "batch": "b2"}])

    assert (tmp_path / "h.json").read_text(encoding="utf-8") == old_text
    assert [p.name for p in tmp_path.iterdir()] == ["h.json"]
    data = json.loads((tmp_path / "h.json").read_text(encoding="utf-8"))
    assert data == [{"src": "a", "dst": "b", "batch": "b1"}]


# ---------- 打包成 exe 后的程序目录 ----------

def test_app_dir_source_mode_is_project_root():
    """源码运行时程序目录就是项目根目录（收纳库默认建在这里）。"""
    assert cfg.app_dir() == os.path.dirname(os.path.abspath(cfg.__file__))
    assert os.path.isdir(cfg.app_dir())


def test_app_dir_frozen_uses_exe_folder(tmp_path, monkeypatch):
    """打包成 exe 后程序目录取 exe 所在目录。

    关键点：绝不能退回临时解包目录——否则默认收纳库会随临时目录一起被删，
    用户整理的资料跟着丢失。
    """
    monkeypatch.setattr(cfg.sys, "frozen", True, raising=False)
    monkeypatch.setattr(cfg.sys, "executable", str(tmp_path / "桌面分类助手.exe"))

    assert cfg.app_dir() == str(tmp_path)
