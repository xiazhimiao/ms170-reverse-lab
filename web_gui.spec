# -*- mode: python ; coding: utf-8 -*-
# v5 Web 版打包：Flask 本地服务 + 浏览器界面 + pystray 托盘
# 前端页面为内嵌字符串（无 templates 目录）；类型.txt 作为数据文件收集

import os

block_cipher = None

a = Analysis(
    ['web_gui.py'],
    pathex=[],
    binaries=[
        # numpy 1.26 的 OpenBLAS DLL 目录（PyInstaller 6.3 hook 漏收集，缺它 exe 内 numpy 导入失败）
        (r'C:\ProgramData\Miniconda3\Lib\site-packages\numpy.libs', 'numpy.libs'),
        # pandas 的 conda 重命名 MSVC 运行时 DLL：必须放 pandas.libs（pandas/__init__.py
        # 的 _delvewheel_patch 从这里 add_dll_directory / 按 .load-order 预加载）
        (r'C:\ProgramData\Miniconda3\Lib\site-packages\pandas.libs', 'pandas.libs'),
    ],
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
        # openpyxl 的第三方硬依赖：缺失会导致 exe 内 xlsx 导出 500（ModuleNotFoundError）
        'et_xmlfile',
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
