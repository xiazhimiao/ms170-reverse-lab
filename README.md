# 靓号查询（微信小程序渠道 API 研究）

> 作者：[xiazhimiao](https://github.com/xiazhimiao) ｜ 项目：[ms170-reverse-lab](https://github.com/xiazhimiao/ms170-reverse-lab)

> ⚠️ **软件仅供学习测试使用，请勿商用！** 本项目仅用于微信小程序逆向与接口协议学习研究，请合理控制查询频率，尊重目标服务。

对「民生靓号」微信小程序（wx33aafb5db3e8214f）渠道 API 的还原研究：从反编译小程序源码出发，还原签名算法与接口协议，实现号码查询、套餐选择、订单确认的完整流程复刻。

## 功能

- 🔍 **靓号查询**：按省份 / 城市 / 等级 / **号码关键词**（如输入 `4` 匹配含 4 的号码）筛选，条件独立可任意组合，自动分页；套餐信息**并发查询**（每页 5 线程加速）；SSE 实时推送进度；等级类型**动态拉取**（rankList 接口 40 种，含 情侣号/生日号/个性号 等中文类型，与小程序一致）
- 💰 **套餐选择（复刻小程序）**：点击号码 → 弹窗直接列出全部可办套餐，**第一个 = 接口返回的默认最优惠套餐**（与小程序 `packagesInfos[0]` 逻辑一致）→ 立即办理 / 切换其他套餐 → 订单确认
- ✅ **订单确认（复刻小程序）**：套餐详情确认 → 确认订阅（演示，实际下单在「民生靓号」小程序内完成）
- 📁 **导出入库（默认 DuckDB）**：导出格式可选 **DuckDB 入库 / Excel / CSV**；勾选「附带更多套餐」追加**一列「其他套餐」**（第 2 个及以后的套餐合并，格式 `套餐名（X元/月）：详情`，默认最优惠套餐留在「套餐」列）；文件名自动带 省份_城市_时间戳
- 🗄️ **数据库管理页**（顶栏按钮 / 托盘「数据库管理」入口）：表列表 → 数据预览分页；**点击单元格直接编辑**；列工具条（点击列头选中 → 整列替换 / 末尾追加 / 开头插入 / 查找替换（支持正则）/ 复制列 / 重命名 / 删除列）；行操作（删除匹配行 / 条件置值 / 新增列）；一键导出当前表 Excel
- ⚙️ **工作流引擎**：把常用的字段处理（追加文字、条件置值、删除匹配行……共 10 种步骤）保存为工作流，**启用后每次导出入库的新表自动执行**；可下载 JSON 发给他人，对方导入即可复刻同样的处理逻辑
- 🎨 **毛玻璃界面**：`backdrop-filter` 玻璃卡片 + 随机二次元图背景（5 源**并发**拉取先到先用 + 程序化晚霞樱花壁纸兜底，单源慢不拖死整体）
- 🖥️ **系统托盘**：托盘常驻，打开界面 / 数据库管理 / 退出
- 🧾 流量专区（分类 → 产品 → 详情 → 物流）、订单查询、右键复制号码

## 快速开始

### 方式一：直接运行 exe（推荐）

从 [Releases](https://github.com/xiazhimiao/ms170-reverse-lab/releases) 下载 `LuckyNumberQuery.exe`（即「靓号查询」，GitHub 资产用 ASCII 文件名），双击运行（自动打开浏览器界面，托盘出现图标）。

### 方式二：源码运行

```bash
pip install -r requirements.txt
python web_gui.py                    # 默认端口 8755，自动打开浏览器
python web_gui.py --port 9000 --no-browser
```

## 使用说明

1. 加载省份 → 选省份/城市/等级（可填号码关键词）→ 「开始查询」，结果实时追加；搜索条件独立，可单选也可组合
2. 点击号码行（或「办理」按钮）→ 套餐选择弹窗（预存 / 月低消 / 最划算套餐 / 更多套餐）
3. 选择套餐 → 「下一步」→ 订单确认弹窗 → 「确认订阅」
4. 「导出」：勾选「附带更多套餐」会异步导出（带进度条）；默认 DuckDB 入库，数据文件 `靓号查询数据.duckdb` 在 exe 同目录
5. 「数据库管理」：预览 / 编辑单元格 / 列与行操作；「工作流设置」：添加步骤 → 保存并启用 → 之后每次导出入库自动执行；下载 JSON 可分享给他人导入
6. ⚠️ 查询间隔建议 ≥2s（最小 1s），避免对服务器造成压力或触发 IP 限流

## 接口协议（逆向还原）

| 接口 | 功能 |
|---|---|
| `qryPhoneList` | 靓号列表（省市/等级/号码关键词 msisdn 筛选分页，行自带 预存 bossPrestore / 月低消 minConsume / rank） |
| `rankList` | 号码类型动态列表（40 种，含中文类型；小程序点击「号码类型」时拉取） |
| `checkMsisdnStatus` | 号码套餐状态（productName / productFee / liuTotal / callTotal / serviceDesc） |
| `qryProductList` | 号码可办套餐列表 |
| `third/qryCategoryList` | 流量产品分类 |
| `third/qryProductList` | 分类下产品列表 |
| `qryKd` | 物流方式（顺丰） |
| `qryOrder` | 订单查询 |

- 签名：`sign = MD5(SALT + JSON.stringify(param 键字典序))`
- 请求体：`{sign, tid, timestamp, param, p}`，UA = `userWxMini`
- base：`https://wechatn.mstelcom.cn`（旧域名 `wechat.ms170.cn` 已下线）

## 目录结构

```
ms170-reverse-lab/
├── web_gui.py              # 主程序（Flask 本地服务 + Web 界面 + 托盘）
├── duckdb_store.py         # DuckDB 本地存储 + 字段操作原语 + 工作流引擎
├── db_page.html            # 数据库管理页（独立页面：编辑/工具条/工作流面板）
├── phone_number_fetcher.py # 渠道 API 封装（自动签名 / 指数退避重试 / 并发）
├── anime_bg.py             # 二次元背景图多源拉取（整体时间预算防拖死）
├── web_gui_test.py         # 冒烟测试（80+ 项检查）
├── web_gui.spec            # PyInstaller 打包配置（产出 靓号查询.exe）
├── 类型.txt                # 等级数据（主程序同目录运行时读取）
├── icon.ico                # 应用图标
├── requirements.txt        # 依赖
├── tools/
│   └── make_icon.py        # 图标生成脚本（PIL 程序化绘制）
└── legacy/                 # 已弃用的 tkinter 版（存档对照）
    ├── phone_number_gui.py
    ├── phone_number_gui.spec
    ├── gui_v4_smoke_test.py
    ├── build_installer.py
    └── bg_preview_v4.png
```

## 免责声明

本项目**仅供学习与测试使用，禁止用于任何商业用途**。逆向研究仅针对自有授权场景。请合理控制查询频率，尊重目标服务，避免对服务器造成压力。使用本项目产生的一切后果由使用者自行承担。
