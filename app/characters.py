"""角色介紹頁（/characters）的內容，依《吱可思大冒險》角色設定。

file 對應 static/img/mascots/ 裡的圖檔；tone 決定卡片底色（style.css 的 .char-<tone>）。
"""

TITLE = "吱可思大冒險"

# 大主角
LEAD = {
    "name": "e度", "file": "main.png", "tone": "peach",
    "tagline": "動作緩慢，但個性溫柔可靠",
    "lines": ["擅長傾聽他人煩惱的守護者", "點子很多能給予實用的意見"],
    "note": "思考時會產生智慧電流",
}

# 好朋友們（依設定圖由左到右）
CAST = [
    {"name": "快讚", "file": "mascot-explorer.png", "tone": "mint",
     "tagline": "急性子",
     "lines": ["特殊技法是找出可以吸引人類的議題，用光速般的速度進行資訊傳播，獲得人類的讚數"]},
    {"name": "歐匿", "file": "mascot-wizard.png", "tone": "lilac",
     "tagline": "可愛又神秘",
     "lines": ["特殊技法是決定控制資訊的匿名性與否，來獲得更多的觸擊率"]},
    {"name": "吱吱", "file": "mascot-chick.png", "tone": "lemon",
     "tagline": "喜歡各種嘗試，愛交朋友，有點自戀",
     "lines": ["有經營直播頻道，富正義感但有時候口快了一些"]},
    {"name": "可可", "file": "mascot-box.png", "tone": "sky",
     "tagline": "沉穩謹慎，有點神經質",
     "lines": ["屬於網海中潛水型的人，總是ㄎㄎ，重視隱私"]},
    {"name": "思思", "file": "mascot-fluffy.png", "tone": "rose",
     "tagline": "愛作白日夢，非常容易相信別人，有禮貌，被打了還會跟你說謝謝的那種人",
     "lines": ["很容易出包，還好身邊有兩位好朋友"]},
]
