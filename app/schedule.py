"""活動議程（依《老師活動順序.pdf》）。

活動期間，首頁只列出『當天』上場的老師，並依此處的順序排列；
卡片顏色依區域（北區／中區／南區／東區／國教署），同區域同色。
以帳號（users.username）對應老師，所以公開網址代碼怎麼設都不影響。
過了午夜（台北時間）自動換成下一天；活動前、活動後首頁照常列出所有老師。
"""
from datetime import date

REGIONS = {
    "north": "北區",
    "central": "中區",
    "south": "南區",
    "east": "東區",
    "k12ea": "國教署",
}

# (日期, [(帳號, 區域), ...])：順序即首頁顯示順序
DAYS = [
    ("2026-11-12", [("t001", "central"),   # 雲林縣－黃欽章
                    ("t002", "south"),     # 臺南市－韋秀麗
                    ("t003", "south"),     # 屏東縣－施孟男
                    ("t004", "east"),      # 宜蘭縣－吳宛如
                    ("t005", "central")]),  # 南投縣－林巧芳
    ("2026-11-13", [("t006", "central"),   # 彰化縣－蘇志祥
                    ("t007", "south"),     # 嘉義縣－洪碧純
                    ("t008", "central"),   # 苗栗縣－林在營
                    ("t009", "south"),     # 嘉義市－謝佳蓉
                    ("t010", "north"),     # 新北市－林書楷
                    ("t011", "k12ea")]),   # 國教署－洪子秀
    ("2026-11-14", [("t012", "north"),     # 桃園市－李文傑
                    ("t013", "north"),     # 基隆市－朱奕勳
                    ("t014", "east"),      # 花蓮縣－鄭閔澤
                    ("t015", "south"),     # 澎湖縣－陳秀芳
                    ("t025", "central"),   # 臺中市－黃仲一
                    ("t017", "k12ea")]),   # 國教署－蔡傑宇
    ("2026-11-15", [("t018", "north"),     # 新竹市－蘇漢哲
                    ("t019", "k12ea"),     # AI 素養教學推動計畫－黃政昱
                    ("t020", "south"),     # 高雄市－楊政儒
                    ("t021", "north"),     # 臺北市－胡雅婷
                    ("t022", "north"),     # 新竹縣
                    ("t023", "north")]),   # 家長宣講
]

WEEKDAYS = "一二三四五六日"


def day_info(day):
    """day 為 YYYY-MM-DD；是活動日就回傳當天資訊，否則回傳 None。"""
    for i, (d, entries) in enumerate(DAYS, 1):
        if d == day:
            dt = date.fromisoformat(d)
            return {"date": d, "number": i, "total": len(DAYS),
                    "label": f"{dt.month}/{dt.day}（{WEEKDAYS[dt.weekday()]}）",
                    "entries": entries}
    return None
