#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
在 Windows 上把《口琴工坊》打成两种发行包，输出到 dist/：

    口琴工坊-v<版本>-免安装版.zip   解压后双击「口琴工坊.exe」；设置和曲库存在同目录的「数据」里，
                                    整个文件夹拷走就能带走
    口琴工坊-v<版本>-安装包.exe     装进 Program Files，带开始菜单 / 桌面快捷方式和卸载；
                                    设置和曲库存在 %LOCALAPPDATA%\\KouqinStudio

两种都不需要电脑上装 Python。exe 自带"以管理员身份运行"的声明（和「启动.bat」一样，
游戏收模拟按键需要），双击时会弹 UAC。

用法（在项目根目录打开终端）::

    python -m pip install pyinstaller
    python packaging\\build_windows.py              # 先跑自检，再打两个包
    python packaging\\build_windows.py --skip-tests

安装包需要 Inno Setup 6（https://jrsoftware.org/isinfo.php）；没装的话只打免安装版。
把 Inno Setup 的简体中文语言文件放成 packaging\\ChineseSimplified.isl，安装界面就是中文，
没有就用英文界面。GitHub 上的自动打包（.github/workflows/build-windows.yml）用的也是这个脚本。

注意：这类自己打的 exe 没有数字签名，又会读键盘状态、模拟按键，杀毒软件和 SmartScreen
很可能误报，需要手动放行。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import zipfile
from typing import List, Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.join(ROOT, "packaging")
BUILD = os.path.join(ROOT, "build")
DIST = os.path.join(ROOT, "dist")

APP = "口琴工坊"
ENTRY = "口琴工坊.py"

#: 跟着程序一起发的文件（许可与免责必须带上，原许可协议第 2.4 条要求）
DOCS = [
    "LICENSE", "DISCLAIMER.md", "README.md", "修改说明.md",
    "功能展示.html", "键位自测台.html", "键位对照表.md",
]
DOC_DIRS = ["示例MIDI", "示例曲谱"]

#: 解压后最先看到的说明（Windows 记事本能直接打开的纯文本）
README_TXT = """口琴工坊 {version}（个人修改版）
================================

【免责声明 —— 使用前必读】
1. 本程序仅供个人学习、技术研究与娱乐使用，严禁在任何在线对局（匹配、排位、多人合作、
   公开房间等有其他真实玩家的模式）中使用，跟弹模式也不例外。只建议在单人模式、练习场、
   已征得所有人同意的自定义房间里用，或者干脆不进游戏、只跑自检。
2. 「自动演奏」靠模拟键盘鼠标输入实现，这类输入可以被游戏的反作弊系统识别。
   使用自动化输入可能违反游戏用户协议，导致账号被警告、限制、临时或永久封禁，
   账号内的虚拟财产和进度可能一并损失。
3. 禁止任何商业用途：不得出售、出租、收费分发、代练，或以展示本工具为主要内容营利。
4. 本程序按"现状"提供，不附带任何担保。因使用本程序产生的一切后果——包括账号封禁、
   数据丢失、设备异常、法律纠纷——全部由使用者本人承担，原作者、修改者及贡献者不承担任何责任。
5. 本程序与《三角洲行动》及其开发商、发行商没有任何关联、合作或授权关系；
   游戏名称等均为其各自所有者的商标。
6. 这是在原项目（github.com/2776525655/delta-force-harmonica-tool）基础上的个人修改版，
   改了什么见「修改说明.md」；原作者没有参与、也没有为这些修改背书。

下载、安装、运行或以任何方式使用本程序，即视为你已阅读并同意同目录的 LICENSE 与 DISCLAIMER.md
全部条款。不同意的，请立即停止使用并删除本程序。

【怎么开始】
1. 双击「口琴工坊.exe」，弹出 UAC 时点「是」（游戏收模拟按键需要管理员权限）。
   第一次运行如果弹出「Windows 已保护你的电脑」，点「更多信息」→「仍要运行」。
2. 在「曲库」页：
   「跟弹」= 游戏画面上浮出按键提示，你自己按，程序一个键都不按；
   「演奏」= 程序自动替你按。
3. 游戏要设成「无边框窗口化」或「窗口化」。任何时候按 F8 停止，F6 暂停 / 继续。

【设置存在哪】
{data_note}

【杀毒软件报毒】
这个 exe 没有数字签名，又会读键盘状态、模拟按键，杀毒软件可能误报。
不放心的话可以装 Python，直接用源码运行（见 README.md）。
"""

PORTABLE_NOTE = "免安装版：存在本文件夹的「数据」里。整个文件夹拷到别的电脑，设置和自编曲子都跟着走。"
INSTALLED_NOTE = "安装版：存在 %LOCALAPPDATA%\\KouqinStudio 里，卸载重装不会丢。"


def app_version() -> str:
    """直接从源码里读版本号（不 import，免得在打包机上触发 Windows 模块的初始化）。"""
    text = open(os.path.join(ROOT, "kouqin", "__init__.py"), encoding="utf-8").read()
    for line in text.splitlines():
        if line.startswith("__version__"):
            return line.split("=", 1)[1].strip().strip("\"'")
    return "0.0.0"


def version_file_text(version: str) -> str:
    """PyInstaller 的 --version-file：exe「属性 → 详细信息」里显示的版本信息。"""
    parts = [int(p) if p.isdigit() else 0 for p in version.split(".")[:3]]
    while len(parts) < 4:
        parts.append(0)
    nums = tuple(parts[:4])
    return f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers={nums}, prodvers={nums}, mask=0x3f, flags=0x0,
                    OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([
      StringTable('080404b0', [
        StringStruct('CompanyName', '口琴工坊 个人修改版（非原作者发布）'),
        StringStruct('FileDescription', '口琴工坊 · 三角洲行动口琴自动演奏 / 跟弹提示'),
        StringStruct('FileVersion', '{version}'),
        StringStruct('InternalName', 'kouqin'),
        StringStruct('LegalCopyright', '见 LICENSE：Copyright (c) 2026 口琴工坊 (Harmonica Studio) 及其贡献者'),
        StringStruct('OriginalFilename', '{APP}.exe'),
        StringStruct('ProductName', '{APP}'),
        StringStruct('ProductVersion', '{version}')])
    ]),
    VarFileInfo([VarStruct('Translation', [2052, 1200])])
  ]
)
"""


def run(cmd: List[str], **kwargs) -> None:
    print("  >", " ".join(f'"{c}"' if " " in c else c for c in cmd))
    subprocess.run(cmd, check=True, cwd=ROOT, **kwargs)


def run_tests() -> None:
    print("\n[1/4] 自检（不会真的发按键）")
    run([sys.executable, ENTRY, "--selftest"])
    run([sys.executable, os.path.join("tools", "verify.py")])


def build_exe(version: str) -> str:
    """PyInstaller 打成「一个文件夹」：启动快，杀软误报也比单文件版少。返回该文件夹路径。"""
    print("\n[2/4] PyInstaller 打包")
    os.makedirs(BUILD, exist_ok=True)
    version_file = os.path.join(BUILD, "version_info.txt")
    with open(version_file, "w", encoding="utf-8") as fp:
        fp.write(version_file_text(version))
    run([
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--name", APP,
        "--windowed",                      # 不留黑窗口
        "--uac-admin",                     # 和「启动.bat」一样申请管理员权限
        "--version-file", version_file,
        "--paths", ROOT,
        "--distpath", os.path.join(BUILD, "dist"),
        "--workpath", os.path.join(BUILD, "work"),
        "--specpath", BUILD,
        ENTRY,
    ])
    folder = os.path.join(BUILD, "dist", APP)
    if not os.path.isfile(os.path.join(folder, APP + ".exe")):
        raise SystemExit(f"没找到打包结果：{folder}")
    return folder


def stage_docs(folder: str, version: str, data_note: str) -> None:
    """把许可、免责、说明和示例放到 exe 旁边。"""
    for name in DOCS:
        shutil.copy2(os.path.join(ROOT, name), os.path.join(folder, name))
    for name in DOC_DIRS:
        target = os.path.join(folder, name)
        if os.path.isdir(target):
            shutil.rmtree(target)
        shutil.copytree(os.path.join(ROOT, name), target)
    # 带 BOM 的 UTF-8：老版本记事本、Inno Setup 的许可页才不会把中文读成乱码
    with open(os.path.join(folder, "先看我.txt"), "w", encoding="utf-8-sig", newline="\r\n") as fp:
        fp.write(README_TXT.format(version=version, data_note=data_note))
    with open(os.path.join(ROOT, "LICENSE"), encoding="utf-8") as src, \
            open(os.path.join(folder, "许可协议.txt"), "w", encoding="utf-8-sig", newline="\r\n") as dst:
        dst.write(src.read())


def make_zip(folder: str, zip_path: str) -> None:
    """打成 zip，里面顶层是「口琴工坊」文件夹。非 ASCII 文件名会带 UTF-8 标记，Win10/11 自带解压能认。"""
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for dirpath, _dirs, files in os.walk(folder):
            for name in sorted(files):
                path = os.path.join(dirpath, name)
                arc = os.path.join(APP, os.path.relpath(path, folder))
                zf.write(path, arc)


def find_iscc() -> Optional[str]:
    found = shutil.which("iscc") or shutil.which("ISCC")
    if found:
        return found
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles"),
                 os.environ.get("LOCALAPPDATA") and os.path.join(os.environ["LOCALAPPDATA"], "Programs")):
        if base:
            candidate = os.path.join(base, "Inno Setup 6", "ISCC.exe")
            if os.path.isfile(candidate):
                return candidate
    return None


def build_installer(folder: str, version: str) -> Optional[str]:
    iscc = find_iscc()
    if not iscc:
        print("  没找到 Inno Setup 6（ISCC.exe），跳过安装包，只出免安装版。")
        return None
    os.makedirs(DIST, exist_ok=True)
    run([iscc, f"/DAppVersion={version}", f"/DSourceDir={folder}", f"/DOutputDir={DIST}",
         os.path.join(HERE, "installer.iss")])
    path = os.path.join(DIST, f"{APP}-v{version}-安装包.exe")
    return path if os.path.isfile(path) else None


def sha256(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fp:
        for block in iter(lambda: fp.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="把口琴工坊打成 Windows 免安装版和安装包")
    parser.add_argument("--skip-tests", action="store_true", help="不跑自检，直接打包")
    args = parser.parse_args(argv)

    if sys.platform != "win32":
        print("只能在 Windows 上打包（PyInstaller 不能跨系统打包）。"
              "没有 Windows 电脑的话，推到 GitHub 用自动打包。")
        return 2

    version = app_version()
    print(f"口琴工坊 v{version} —— 打包 Windows 版")
    if not args.skip_tests:
        run_tests()
    folder = build_exe(version)

    print("\n[3/4] 免安装版")
    stage_docs(folder, version, PORTABLE_NOTE)
    os.makedirs(DIST, exist_ok=True)
    portable = os.path.join(DIST, f"{APP}-v{version}-免安装版.zip")
    make_zip(folder, portable)

    print("\n[4/4] 安装包")
    stage_docs(folder, version, INSTALLED_NOTE)     # 安装版的「先看我.txt」写的是另一个存放位置
    installer = build_installer(folder, version)

    print("\n完成：")
    for path in (portable, installer):
        if path:
            size = os.path.getsize(path) / 1024 / 1024
            print(f"  {os.path.relpath(path, ROOT)}  {size:.1f} MB  sha256={sha256(path)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
