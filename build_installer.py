#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AI智能靓号查询系统 - 自动打包脚本
使用清华镜像源，一键打包成可执行文件
"""

import os
import sys
import subprocess
import shutil
import zipfile
from pathlib import Path

def run_command(command, description):
    """运行命令并显示进度"""
    print(f"🔄 {description}...")
    try:
        result = subprocess.run(command, shell=True, check=True, capture_output=True, text=True)
        print(f"✅ {description} 完成")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ {description} 失败: {e}")
        print(f"错误输出: {e.stderr}")
        return False

def create_requirements_file():
    """创建requirements.txt文件"""
    requirements = """requests>=2.31.0
pandas>=2.0.0
openpyxl>=3.1.0
pyinstaller>=5.13.0"""
    
    with open('requirements.txt', 'w', encoding='utf-8') as f:
        f.write(requirements)
    print("✅ requirements.txt 已创建")

def install_dependencies():
    """使用清华镜像源安装依赖"""
    print("🚀 开始安装依赖库...")
    
    # 使用sys.executable来确保使用正确的Python解释器
    python_exe = sys.executable
    
    # 升级pip
    if not run_command(f'"{python_exe}" -m pip install --upgrade pip -i https://pypi.tuna.tsinghua.edu.cn/simple', "升级pip"):
        return False
    
    # 安装依赖
    if not run_command(f'"{python_exe}" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple', "安装依赖库"):
        return False
    
    return True

def create_spec_file():
    """创建PyInstaller配置文件"""
    spec_content = '''# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['phone_number_gui.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[
        'tkinter',
        'tkinter.ttk',
        'tkinter.messagebox',
        'tkinter.filedialog',
        'tkinter.scrolledtext',
        'threading',
        'time',
        'json',
        'pandas',
        'openpyxl',
        'requests',
        'urllib3',
        'certifi',
        'charset_normalizer',
        'idna'
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
    name='AI智能靓号查询系统',
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
'''
    
    with open('phone_number_gui.spec', 'w', encoding='utf-8') as f:
        f.write(spec_content)
    print("✅ PyInstaller配置文件已创建")

def build_executable():
    """打包成可执行文件"""
    print("🔨 开始打包可执行文件...")
    
    # 使用sys.executable来确保使用正确的Python解释器
    python_exe = sys.executable
    
    # 使用spec文件打包
    if not run_command(f'"{python_exe}" -m PyInstaller --clean phone_number_gui.spec', "打包可执行文件"):
        return False
    
    return True

def create_installer():
    """创建独立的exe文件"""
    print("📦 创建独立exe文件...")
    
    # 检查可执行文件是否存在=
    dist_dir = Path("dist")
    exe_file = dist_dir / "AI智能靓号查询系统.exe"
    
    if not exe_file.exists():
        print("❌ 找不到可执行文件")
        return False
    
    # 复制到当前目录，重命名为更简洁的名称
    final_exe = Path("AI智能靓号查询系统.exe")
    shutil.copy2(exe_file, final_exe)
    print("✅ 独立exe文件已创建: AI智能靓号查询系统.exe")
    
    # 显示文件大小
    file_size = final_exe.stat().st_size / (1024 * 1024)  # MB
    print(f"📊 文件大小: {file_size:.1f} MB")
    
    return True



def main():
    """主函数"""
    print("=" * 60)
    print("🤖 AI智能靓号查询系统 - 自动打包工具")
    print("=" * 60)
    print()
    
    # 检查Python版本
    if sys.version_info < (3, 7):
        print("❌ 需要Python 3.7或更高版本")
        return
    
    print(f"✅ Python版本: {sys.version}")
    print()
    
    # 创建requirements.txt
    create_requirements_file()
    
    # 安装依赖
    if not install_dependencies():
        print("❌ 依赖安装失败，打包终止")
        return
    
    # 创建spec文件
    create_spec_file()
    
    # 打包可执行文件
    if not build_executable():
        print("❌ 打包失败")
        return
    
    # 创建安装包
    if not create_installer():
        print("❌ 安装包创建失败")
        return
    
    print()
    print("🎉 打包完成！")
    print("📁 独立exe文件: AI智能靓号查询系统.exe")
    print()
    print("💡 提示：")
    print("   1. 将exe文件发送给客户")
    print("   2. 客户双击exe文件即可直接运行")
    print("   3. 无需安装Python和任何依赖库")
    print("   4. 无需解压，无需其他文件")

if __name__ == "__main__":
    main() 