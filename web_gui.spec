# -*- mode: python ; coding: utf-8 -*-
# v5 Web 版打包：Flask 本地服务 + 浏览器界面 + pystray 托盘
# 前端页面为内嵌字符串（无 templates 目录）；类型.txt 作为数据文件收集

import os

block_cipher = None

a = Analysis(
    ['web_gui.py'],
    pathex=[],
    binaries=[],
    datas=[('类型.txt', '.')],
    hiddenimports=[
        'flask',
        'werkzeug',
        'werkzeug.serving',
        'jinja2',
        'markupsafe',
        'click',
        'itsdangerous',
        'blinker',
        'importlib_metadata',
        'pystray',
        'requests',
        'urllib3',
        'certifi',
        'charset_normalizer',
        'idna',
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'PIL.ImageFilter',
        'PIL.ImageTk',
        'numpy',
        'pandas',
        'openpyxl',
        'anime_bg',
        'phone_number_fetcher',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='靓号查询',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico' if os.path.exists('icon.ico') else None,
)
