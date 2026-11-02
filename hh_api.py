"""HolliHop API V2 klienti."""
import json
import time
import urllib.error
import urllib.parse
import urllib.request


class HHError(Exception):
    pass


class HHClient:
    PAGE = 1000

    def __init__(self, domain, key):
        domain = domain.replace("https://", "").replace("http://", "").strip().strip("/")
        if "." not in domain:
            domain += ".t8s.ru"
        self.base = f"https://{domain}/Api/V2/"
        self.key = key

    def call(self, method, **params):
        # Agent faqat o'qiydi: CRM'da hech narsa o'zgartirilmasligi uchun Get* dan boshqa metodlar taqiqlangan
        if not method.startswith("Get"):
            raise HHError(f"{method}: ruxsat yo'q, agent faqat ma'lumot o'qiydi")
        query = {}
        for k, v in params.items():
            if v is None:
                continue
            query[k] = ("true" if v else "false") if isinstance(v, bool) else v
        query["authkey"] = self.key
        url = self.base + method + "?" + urllib.parse.urlencode(query)
        error = None
        for attempt in range(3):
            try:
                with urllib.request.urlopen(url, timeout=90) as resp:
                    return json.loads(resp.read().decode("utf-8") or "{}")
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", "replace")[:300]
                if e.code in (401, 403):
                    raise HHError(f"{method}: API kalit noto'g'ri yoki ruxsat yo'q (HTTP {e.code}) {body}")
                error = f"{method}: HTTP {e.code} {body}"
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                error = f"{method}: {e}"
            time.sleep(3 * (attempt + 1))
        raise HHError(error)

    def paged(self, method, key, **params):
        items, skip, first = [], 0, None
        while True:
            page = self.call(method, skip=skip, take=self.PAGE, **params).get(key) or []
            # HolliHop ba'zan skip/take'ni e'tiborsiz qoldirib, hammasini bir yo'la qaytaradi
            if not page or page[0] == first:
                return items
            items.extend(page)
            if len(page) != self.PAGE:
                return items
            first, skip = page[0], skip + len(page)

    def groups(self, types, status):
        return self.paged("GetEdUnits", "EdUnits", types=types, statuses=status)

    def group_by_id(self, group_id, types):
        return self.call("GetEdUnits", id=group_id, types=types).get("EdUnits") or []

    def ed_unit_students(self, **params):
        return self.paged("GetEdUnitStudents", "EdUnitStudents", **params)
