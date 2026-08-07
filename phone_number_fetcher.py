import requests
import json
import time
import uuid
import hashlib
import urllib.parse
from typing import Optional, Dict, List, Any


# 新版接口协议（2026-08-07 从小程序 wx33aafb5db3e8214f 反编译还原）
# - 后端域名已从 wechat.ms170.cn 迁移到 wechatn.mstelcom.cn（旧服务器已下线）
# - sign 不再是抓包抄取的会话凭据，而是可计算的：
#     sign = MD5(SALT + JSON.stringify(param键按字典序排序))
#   SALT 硬编码在小程序 utils/netApiForChannel.js 中
# - tid 为动态 uuid，无需抓包
# - 请求头 User-Agent 必须为 "userWxMini"，无需登录 token（渠道版接口）
SALT = "e310e26c3ab0f2adb22222dd242eef11"
# 渠道二维码（小程序入口场景值，长期有效；miniQrCheck 会返回新的 qrCode）
CHANNEL_QRCODE = "TXpjNU9RPT0mTnpnNA=="
BASE_URL = "https://wechatn.mstelcom.cn"
# 小程序 appid 与下单页路径（反编译 pages/index/index.js USER_MENU_14 跳转）
APP_ID = "wx33aafb5db3e8214f"
ORDER_PAGE_PATH = "pages/ChangePackageModule/PackageChooseCPMPage/PackageChooseCPMPage"


def make_order_link(msisdn: str) -> str:
    """构造小程序明文 URL Scheme 下单链接 weixin://dl/business/?appid=&path=&query=

    与小程序源码内跳转完全一致：PackageChooseCPMPage?orderJson={"msisdn":号码}
    （页面 onLoad 解析 orderJson 后自行查询该号码可办套餐，PackageChooseCPMPage.js:47-49）
    注：明文 scheme 需小程序主体在 MP 后台「隐私与安全-明文Scheme拉起此小程序」
    声明后微信端才识别；链接 ~150 字符，远低于 QR 码容量上限 2953 字节。
    """
    query = urllib.parse.urlencode({"orderJson": json.dumps(
        {"msisdn": msisdn}, ensure_ascii=False, separators=(",", ":"))})
    return f"weixin://dl/business/?appid={APP_ID}&path={ORDER_PAGE_PATH}&query={query}"


def _calc_sign(param: Dict) -> str:
    """复刻 signUtil.js: sign = MD5(SALT + JSON.stringify(param键排序后))"""
    sorted_param = dict(sorted(param.items()))
    raw = SALT + json.dumps(sorted_param, separators=(",", ":"), ensure_ascii=False)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _make_request_body(param: Dict) -> Dict:
    """构造新版请求体: {sign, tid, timestamp, param, p}"""
    return {
        "sign": _calc_sign(param),
        "tid": str(uuid.uuid4()),
        "timestamp": int(time.time() * 1000),
        "param": param,
        "p": 1,
    }


class PhoneNumberFetcher:
    """民生靓号查询工具类（已适配新版接口）"""

    def __init__(self, sign: str = "", tid: str = ""):
        """
        初始化查询工具

        Args:
            sign: 旧版签名凭据（已废弃，仅保留参数兼容 GUI，实际自动生成）
            tid: 旧版交易ID（已废弃，仅保留参数兼容 GUI，实际自动生成）
        """
        self.qr_code = ""
        self.base_url = BASE_URL
        self.headers = {
            "Accept": "*/*",
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Connection": "keep-alive",
            "Content-Type": "application/json;charset=utf-8",
            "Host": "wechatn.mstelcom.cn",
            "Referer": "https://servicewechat.com/wx33aafb5db3e8214f/169/page-frame.html",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "cross-site",
            "User-Agent": "userWxMini",
        }

    def _get_timestamp(self) -> int:
        """获取当前时间戳"""
        return int(time.time() * 1000)

    def _make_request(self, url: str, request_data: Dict, retries: int = 2) -> Optional[Dict]:
        """
        发送HTTP请求（自动生成 sign/tid），失败自动重试（指数退避）

        Args:
            url: 请求URL
            request_data: 请求数据（param 部分）
            retries: 失败重试次数（默认 2，即最多发 3 次）

        Returns:
            响应数据字典，失败返回None
        """
        # 禁用系统代理，避免被残留代理劫持
        session = requests.Session()
        session.trust_env = False
        last_err = ""
        for attempt in range(retries + 1):
            try:
                body = _make_request_body(request_data)
                # 计算Content-Length
                request_json = json.dumps(body, separators=(',', ':'))
                content_length = len(request_json.encode('utf-8'))
                headers = self.headers.copy()
                headers["Content-Length"] = str(content_length)

                print(f"请求URL: {url}")
                print(f"请求数据JSON: {request_json}")
                print(f"Content-Length: {content_length}")

                # 发送POST请求（禁用系统代理，避免被残留代理劫持）
                response = session.post(url, json=body, headers=headers, timeout=15)
                response.raise_for_status()

                # 解析响应数据
                result_data = response.json()
                print(f"API响应: {json.dumps(result_data, ensure_ascii=False, indent=2)}")

                return result_data

            except requests.exceptions.Timeout as e:
                last_err = f"请求超时: {e}"
            except requests.exceptions.ConnectionError as e:
                last_err = f"连接失败: {e}"
            except requests.exceptions.RequestException as e:
                last_err = f"请求异常: {e}"
            except json.JSONDecodeError as e:
                last_err = f"JSON解析失败: {e}"
            except Exception as e:
                last_err = f"发生错误: {e}"
            if attempt < retries:
                # 指数退避：第1次重试等 1.5s，第2次等 3s（放慢频率，避免加重服务器压力）
                wait = 1.5 * (2 ** attempt)
                print(f"{last_err}，{wait:.1f}s 后重试（{attempt + 1}/{retries}）")
                time.sleep(wait)
        print(f"请求最终失败: {last_err}")
        return None

    def get_qr_code(self) -> bool:
        """
        获取二维码（渠道码校验，返回新的 qrCode）

        Returns:
            是否成功获取二维码
        """
        url = f"{self.base_url}/agentCrm/v1/h5/maker/miniQrCheck"

        request_data = {
            "qrCode": CHANNEL_QRCODE
        }

        print("正在获取二维码...")
        result = self._make_request(url, request_data)

        if result and result.get("data", {}).get("qrCode"):
            self.qr_code = result["data"]["qrCode"]
            print(f"成功获取二维码: {self.qr_code}")
            return True
        else:
            print(f"获取二维码失败: {result}")
            return False

    def get_rank_list(self) -> List[str]:
        """号码类型列表（rankList 接口动态返回，含 情侣号/生日号/个性号 等中文类型）

        小程序 PhoneNumChoose.requestPhoneNumTypeList 调 /no/duo/rankList（flag=1）——
        h5 渠道前缀实测需带 qrCode。接口失败返回 []。
        """
        if not self.qr_code and not self._ensure_qr():
            print("请先获取二维码")
            return []
        try:
            result = self._make_request(
                f"{self.base_url}/agentCrm/v1/h5/maker/rankList", {"qrCode": self.qr_code})
            if result and result.get("code") == 10200:
                data = result.get("data", [])
                return [str(v) for v in data] if isinstance(data, list) else []
        except Exception:
            pass
        return []

    def get_phone_numbers(
        self,
        page: int = 1,
        province: Optional[str] = None,
        city: Optional[str] = None,
        phone_type: Optional[str] = None,
        msisdn: Optional[str] = None,
        series: str = "h5"
    ) -> List[Dict[str, str]]:
        """
        获取手机号码列表

        Args:
            page: 页数
            province: 省份ID（可选）
            city: 城市ID（可选）
            phone_type: 号码类型（可选）
            msisdn: 号码关键词搜索（可选，如 "4" 匹配含 4 的号码；与地区/类型独立可组合）
            series: 系列（h5=靓号专区 / liu=流量卡专区）

        Returns:
            包含手机号码信息的字典列表
        """
        if not self.qr_code:
            print("请先获取二维码")
            return []

        url = f"{self.base_url}/agentCrm/v1/h5/maker/{series}/qryPhoneList"
        if series == "h5":
            url = f"{self.base_url}/agentCrm/v1/h5/maker/qryPhoneList"

        # 构建参数数据（对齐小程序 requestPhoneNumList：isGreate=1，msisdn 关键词）
        param_data = {
            "qrCode": self.qr_code,
            "showCount": 20,
            "currentPage": page,
            "msisdn": msisdn or "",
            "isGreate": 1,
            "provinceId": province or "",
            "cityId": city or ""
        }

        # 如果指定了类型，添加到参数中
        if phone_type:
            param_data["rank"] = phone_type

        print("正在获取手机号码列表...")
        result = self._make_request(url, param_data)

        if not result or result.get("code") != 10200:
            print(f"查询失败: {result}")
            return []

        data = result.get("data", {}) or {}
        phone_list = data.get("list", [])

        if not phone_list:
            # 检查分页信息
            page_info = data.get("page", {}) or {}
            current_page = page_info.get("currentPage", 0)
            total_page = page_info.get("totalPage", 0)

            if current_page > total_page and total_page > 0:
                print(f"已超出总页数范围：当前页 {current_page}，总页数 {total_page}")
            else:
                print("没有找到手机号码数据")
            return []

        result_list = []

        # 处理每个手机号码
        for i, phone_info in enumerate(phone_list):
            # 套餐信息（checkMsisdnStatus data：productName/productFee/liuTotal/callTotal/serviceDesc）
            package_info = self.get_package_info(phone_info.get("msisdn", ""), series=series)
            pkg = (package_info or {}).get("data", {}) or {}

            result_list.append(self._build_row(i + 1, phone_info, pkg))

            print(f"处理第 {i + 1} 个号码: {phone_info.get('msisdn', '')}")

        return result_list

    @staticmethod
    def _build_row(index: int, phone_info: Dict, pkg: Dict) -> Dict:
        """号码行：列表自带字段（预存/月低消/等级）+ checkMsisdnStatus 套餐字段（套餐名/月费/额度/详情）"""
        return {
            "index": index,
            "phone_number": phone_info.get("msisdn", ""),
            "province": phone_info.get("provinceName", ""),
            "city": phone_info.get("cityName", ""),
            "rank": phone_info.get("rank", ""),
            "bossPrestore": phone_info.get("bossPrestore") or pkg.get("bossPrestore") or 0,
            "minConsume": phone_info.get("minConsume") or 0,
            "productName": pkg.get("productName", ""),
            "productFee": pkg.get("productFee") or 0,
            "liuTotal": pkg.get("liuTotal", ""),
            "callTotal": pkg.get("callTotal", ""),
            "package": pkg.get("serviceDesc", ""),
        }

    def get_phone_numbers_parallel(
        self,
        page: int = 1,
        province: Optional[str] = None,
        city: Optional[str] = None,
        phone_type: Optional[str] = None,
        msisdn: Optional[str] = None,
        series: str = "h5",
        workers: int = 5,
    ) -> Optional[List[Dict[str, str]]]:
        """并行版 get_phone_numbers：每页号码的套餐信息用线程池并发查询（5 并发）

        返回：
            请求失败 → None（区别于无数据）
            页码有效但无号码 → []
            成功 → rows 列表（字段与 get_phone_numbers 一致：index/phone_number/province/city/package/link）
        """
        from concurrent.futures import ThreadPoolExecutor

        if not self.qr_code:
            print("请先获取二维码")
            return None

        url = f"{self.base_url}/agentCrm/v1/h5/maker/{series}/qryPhoneList"
        if series == "h5":
            url = f"{self.base_url}/agentCrm/v1/h5/maker/qryPhoneList"

        param_data = {
            "qrCode": self.qr_code,
            "showCount": 20,
            "currentPage": page,
            "msisdn": msisdn or "",
            "isGreate": 1,
            "provinceId": province or "",
            "cityId": city or ""
        }
        if phone_type:
            param_data["rank"] = phone_type

        result = self._make_request(url, param_data)
        if not result or result.get("code") != 10200:
            print(f"查询失败: {result}")
            return None
        phone_list = (result.get("data", {}) or {}).get("list", [])
        if not phone_list:
            return []

        def load(phone_info):
            phone_no = phone_info.get("msisdn", "")
            try:
                package_info = self.get_package_info(phone_no, series=series)
                pkg = (package_info or {}).get("data", {}) or {}
            except Exception:
                pkg = {}
            return self._build_row(0, phone_info, pkg)

        with ThreadPoolExecutor(max_workers=workers) as pool:
            rows = list(pool.map(load, phone_list))
        for i, r in enumerate(rows):
            r["index"] = i + 1
        return rows

    def get_package_info(self, phone_number: str, series: str = "h5") -> Optional[Dict]:
        """
        获取手机号码套餐信息

        Args:
            phone_number: 手机号码
            series: 系列（h5=靓号专区 / liu=流量卡专区）

        Returns:
            套餐信息字典，失败返回None
        """
        if not self.qr_code:
            print("请先获取二维码")
            return None

        url = f"{self.base_url}/agentCrm/v1/h5/maker/{series}/checkMsisdnStatus"
        if series == "h5":
            url = f"{self.base_url}/agentCrm/v1/h5/maker/checkMsisdnStatus"

        request_data = {
            "qrCode": self.qr_code,
            "msisdn": phone_number
        }

        return self._make_request(url, request_data)

    # ---------- 2026-08-07 扩展：渠道版更多接口（流量专区 / 订单查询） ----------

    def _ensure_qr(self) -> bool:
        """确保已获取有效 qrCode，未获取则自动换取"""
        if not self.qr_code:
            return self.get_qr_code()
        return True

    def get_categories(self) -> List[Dict]:
        """流量专区产品分类（third/qryCategoryList），参数 {qrCode}"""
        if not self._ensure_qr():
            return []
        result = self._make_request(
            f"{self.base_url}/agentCrm/v1/h5/maker/third/qryCategoryList",
            {"qrCode": self.qr_code})
        if not result or result.get("code") != 10200:
            print(f"分类查询失败: {result}")
            return []
        data = result.get("data") or []
        return data if isinstance(data, list) else []

    def get_products(self, category_id) -> List[Dict]:
        """流量专区产品列表（third/qryProductList），参数 {qrCode, categroyId}"""
        if not self._ensure_qr():
            return []
        result = self._make_request(
            f"{self.base_url}/agentCrm/v1/h5/maker/third/qryProductList",
            {"qrCode": self.qr_code, "categroyId": category_id})
        if not result or result.get("code") != 10200:
            print(f"产品列表查询失败: {result}")
            return []
        data = result.get("data") or []
        return data if isinstance(data, list) else []

    def get_product_info(self, product_id) -> Optional[Dict]:
        """流量产品详情（third/qryProductInfo），参数 {qrCode, productId}"""
        if not self._ensure_qr():
            return None
        result = self._make_request(
            f"{self.base_url}/agentCrm/v1/h5/maker/third/qryProductInfo",
            {"qrCode": self.qr_code, "productId": product_id})
        if not result or result.get("code") != 10200:
            print(f"产品详情查询失败: {result}")
            return None
        return result.get("data") or {}

    def get_flow_numbers(self, page: int = 1, province: str = "", city: str = "",
                         msisdn: str = "") -> List[Dict]:
        """流量号码列表（third/msisdn/qryMsisdnList），参数 {qrCode, showCount, currentPage, msisdn, provinceId, cityId}"""
        if not self._ensure_qr():
            return []
        result = self._make_request(
            f"{self.base_url}/agentCrm/v1/h5/maker/third/msisdn/qryMsisdnList",
            {"qrCode": self.qr_code, "showCount": 20, "currentPage": page,
             "msisdn": msisdn, "provinceId": province, "cityId": city})
        if not result or result.get("code") != 10200:
            print(f"流量号码查询失败: {result}")
            return []
        data = result.get("data") or {}
        return data.get("list", []) if isinstance(data, dict) else []

    def get_flow_packages(self, category_id, product_id) -> List[Dict]:
        """流量套餐列表（third/msisdn/qryPackageList），参数 {qrCode, categroyId, productId}"""
        if not self._ensure_qr():
            return []
        result = self._make_request(
            f"{self.base_url}/agentCrm/v1/h5/maker/third/msisdn/qryPackageList",
            {"qrCode": self.qr_code, "categroyId": category_id, "productId": product_id})
        if not result or result.get("code") != 10200:
            print(f"流量套餐查询失败: {result}")
            return []
        data = result.get("data") or []
        return data if isinstance(data, list) else []

    def get_products_for_number(self, msisdn: str) -> List[Dict]:
        """号码可办套餐列表（h5 qryProductList），参数 {qrCode, msisdn}"""
        if not self._ensure_qr():
            return []
        result = self._make_request(
            f"{self.base_url}/agentCrm/v1/h5/maker/qryProductList",
            {"qrCode": self.qr_code, "msisdn": msisdn})
        if not result or result.get("code") != 10200:
            print(f"号码套餐查询失败: {result}")
            return []
        data = result.get("data") or []
        return data if isinstance(data, list) else []

    def get_kd(self) -> List[Dict]:
        """物流方式列表（qryKd），参数 {qrCode}"""
        if not self._ensure_qr():
            return []
        result = self._make_request(
            f"{self.base_url}/agentCrm/v1/h5/maker/qryKd",
            {"qrCode": self.qr_code})
        if not result or result.get("code") != 10200:
            print(f"物流方式查询失败: {result}")
            return []
        data = result.get("data") or []
        return data if isinstance(data, list) else []

    def get_orders(self, cardno: str, receiver_phone: str) -> List[Dict]:
        """订单查询（qryOrder），参数 {qrCode, cardno, receiverPhone}"""
        if not self._ensure_qr():
            return []
        result = self._make_request(
            f"{self.base_url}/agentCrm/v1/h5/maker/qryOrder",
            {"qrCode": self.qr_code, "cardno": cardno, "receiverPhone": receiver_phone})
        if not result or result.get("code") != 10200:
            print(f"订单查询失败: {result}")
            return []
        data = result.get("data") or []
        return data if isinstance(data, list) else []

    def get_share_info(self, page_code: str = "CHANNEL_GDLL_PRODUCT_LIST") -> Optional[Dict]:
        """页面分享信息（sharePageInfo），参数 {qrCode, pageCode}"""
        if not self._ensure_qr():
            return None
        result = self._make_request(
            f"{self.base_url}/agentCrm/v1/h5/maker/sharePageInfo",
            {"qrCode": self.qr_code, "pageCode": page_code})
        if not result or result.get("code") != 10200:
            print(f"分享信息查询失败: {result}")
            return None
        return result.get("data") or {}

    def get_provinces(self) -> List[Dict[str, str]]:
        """
        获取省份列表

        Returns:
            包含省份信息的字典列表
        """
        url = f"{self.base_url}/agentCrm/v1/h5/dictionaries/getDictionaryByCode"

        request_data = {
            "codes": "AREA_CODE_SYNC"
        }

        result = self._make_request(url, request_data)

        if not result or result.get("code") != 10200:
            print(f"没有找到省份数据: {result}")
            return []

        # 新版响应直接返回 data.value1（无 result 包装层）
        province_list = result.get("data", {}).get("value1", [])
        provinces = []

        for province_info in province_list:
            if isinstance(province_info, dict):
                provinces.append({
                    "name": province_info.get("areaName", ""),
                    "code": province_info.get("areaCode", "")
                })

        return provinces

    def get_cities(self, province_id: str) -> List[Dict[str, str]]:
        """
        获取城市列表

        Args:
            province_id: 省份ID

        Returns:
            包含城市信息的字典列表
        """
        # 城市数据也在省份API中，需要先获取省份数据然后查找对应的城市
        url = f"{self.base_url}/agentCrm/v1/h5/dictionaries/getDictionaryByCode"

        request_data = {
            "codes": "AREA_CODE_SYNC"
        }

        result = self._make_request(url, request_data)

        if not result or result.get("code") != 10200:
            print(f"没有找到城市数据: {result}")
            return []

        # 在省份数据中查找对应省份的城市列表
        province_list = result.get("data", {}).get("value1", [])
        cities = []

        for province_info in province_list:
            if isinstance(province_info, dict):
                province_code = province_info.get("areaCode", "")
                if province_code == province_id:
                    # 找到匹配的省份，获取其城市列表
                    child_list = province_info.get("childList", [])
                    for city_info in child_list:
                        if isinstance(city_info, dict):
                            cities.append({
                                "name": city_info.get("areaName", ""),
                                "code": city_info.get("areaCode", "")
                            })
                    break  # 找到省份后跳出循环

        return cities

    def search_phone_numbers(
        self,
        page: int = 1,
        province: Optional[str] = None,
        city: Optional[str] = None,
        phone_type: Optional[str] = None
    ) -> List[Dict[str, str]]:
        """
        搜索手机号码（自动获取二维码）

        Args:
            page: 页数
            province: 省份ID（可选）
            city: 城市ID（可选）
            phone_type: 号码类型（可选）

        Returns:
            包含手机号码信息的字典列表
        """
        # 如果没有二维码，先获取
        if not self.qr_code:
            if not self.get_qr_code():
                return []

        return self.get_phone_numbers(page, province, city, phone_type)

    def print_phone_numbers(self, phone_numbers: List[Dict[str, str]]):
        """
        打印手机号码信息

        Args:
            phone_numbers: 手机号码列表
        """
        if phone_numbers:
            print(f"成功获取到 {len(phone_numbers)} 个手机号码:")
            for phone in phone_numbers:
                print(f"序号: {phone['index']}")
                print(f"手机号: {phone['phone_number']}")
                print(f"省份: {phone['province']}")
                print(f"城市: {phone['city']}")
                print(f"套餐: {phone['package']}")
                print(f"链接: {phone['link']}")
                print("-" * 50)
        else:
            print("没有获取到手机号码数据")


# 使用示例
if __name__ == "__main__":
    # 创建查询工具实例（新版自动生成签名，无需填写 sign/tid）
    fetcher = PhoneNumberFetcher()

    # 搜索手机号码
    phone_numbers = fetcher.search_phone_numbers(page=1)

    # 打印结果
    fetcher.print_phone_numbers(phone_numbers)
