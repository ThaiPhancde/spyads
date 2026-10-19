"""Hand labels (2026-10-13) for scripts/search_eval.json — relevant = the product is what the intent asks for.
Ambiguous → not relevant. Unknown products judged from their ad text (ids)."""
import json

P = r"D:\ToolSpy\apps\api\scripts\search_eval.json"
FENG = ["Retro 999 Silver Feng Shui Bracelet", "The Golden Mantra Pixiu Bracelet", "Fortified FengShui BRACELET AND RING",
        "Harmonious Life Lucky Turtle Bracelet", "Fengshui Pixiu Obsidian bracelet", "Anti-evil eye bracelets",
        "Buy1 Get1 Pi-xiu Lucky Bracelet", "Lucky Pixiu Bracelet", "Red Clover Bracelet"]
OK = {
    "feng shui bracelet": (FENG, set()),
    "posture corrector": (["Posture Corrector Bra", "Posturecue", "Best Posture Support", "Wirelebra Faf", "Staidrept",
                           "Adjustable Unisex Posture Corrector", "Fitness Heavy Shoulder Straps", "Truealign X Back Brace"], set()),
    "sunscreen": (["Kem Chống Nắng Tone-Up", "Bảo Vệ Da Tối Ưu Cùng Kem", "Glow Hồng Tự Nhiên", "Chọn Đúng Loại Kem Chống Nắng",
                   "Bên Da Dùng Foundation", "Tone Up Sunscreen", "Feels Like A Moisturizer", "Kitty'S Fave Sunscreen",
                   "SKINEVER Body Brightening Sunscreen", "Sunscreen Foundation"], {4016}),
    "massage gun": (["Cuifati Head Massage Gun", "Fascia Gun Handle", "Deep Tissue Massage Gun", "A 6-Head Massager Typical",
                     "LEERCON Mini Muscle Massage Gun", "Hot And Cold Fascia Gun"], set()),
    "perfume": (["عطور", "عطر", "7 عطور", "المجموعة المميزة", "تدورين على عطر", "Date Night Fragrance", "Fav Perfume Balm",
                 "A Perfect Fragrance", "Profumo Con Feromoni", "Burberry Gold Eau De Parfum", "Moon Perfume Boutique"], {1718}),
    "teeth whitening": (["Whitening Strips & Pen", "Perfume Toothpaste"], {5708, 9552, 8027}),
}
rows = json.load(open(P, encoding="utf-8"))
for r in rows:
    names, ids = OK[r["intent"]]
    for mode in ("before", "after"):
        for x in r[mode]["top10"]:
            x["ok"] = x["id"] in ids or (x["name"] != "Chưa xác định sản phẩm" and any(x["name"].startswith(n) for n in names))
        # an empty slot in a top 10 is a miss: pad so P@10 divides by 10
json.dump(rows, open(P, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for r in rows:
    b = sum(x["ok"] for x in r["before"]["top10"])
    a = sum(x["ok"] for x in r["after"]["top10"])
    print(f"{r['query']!r:24s} P@10 before {b}/10  after {a}/10")
