# HolliHop Audit Agent

HolliHop CRM'ni har 10 daqiqada tekshiradi va admin xatoliklarini Telegram kanalga yuboradi.

## Nimalarni nazorat qiladi
| Xato | Qoida |
|---|---|
| 🗑 Guruh o'chirildi | Guruh HolliHop'dan yo'qolsa |
| ⛔ Guruh muddatidan oldin to'xtatildi | 1 yil to'lmasdan "To'xtatilgan" yoki "Tugatilgan" holatiga o'tkazilsa (sababi tahlil qilinadi) |
| ✂️ Jadval muddatidan oldin tugatildi | Jadval tugash sanasi qisqartirilib, jami davomiylik 1 yildan kam bo'lib qolsa |
| 📅 Jadval 1 yildan qisqa tuzilgan | Yangi guruh jadvali 1 yildan qisqa tuzilsa |
| 🚫 Yangi o'quvchi boshlangan guruhga qo'shildi | Guruhda 3 tadan ko'p dars o'tib bo'lgan, o'quvchi esa oldin boshqa guruhda o'qimagan bo'lsa. Boshqa guruhdan ko'chirilgan o'quvchi xato hisoblanmaydi |
| 🔄 Ustoz almashtirildi | Guruh ustozi har safar almashtirilganda |
| 📵 Kelmagan o'quvchiga bog'lanilmadi | Kelmagan kunining o'zida, 20:00 gacha, davomat izohiga sabab yozilmagan bo'lsa |

## Xabarlar jadvali
- **12:00 va 17:00**: shu vaqtgacha aniqlangan xatoliklar
- **18:00**: kunlik hisobot
- **20:00**: bog'lanilmagan kelmagan o'quvchilar ro'yxati
- **Shanba 17:00**: haftalik hisobot, filiallar reytingi va xulosa

18:00 dan keyin boshlangan darslarda kelmagan o'quvchilar ertasi kuni soat 12:00 da tekshiriladi.

## O'rnatish
1. `.env` faylini oching va `HH_DOMAIN`, `HH_API_KEY`, `TELEGRAM_BOT_TOKEN` ni to'ldiring.
2. Botni kanalga admin qilib qo'shing va kanalga biror xabar yozing.
3. `python main.py chat-id` buyrug'i kanal ID'sini ko'rsatadi. Uni `TELEGRAM_CHAT_ID` ga yozing.
4. `python main.py test` bilan ulanishni tekshiring.
5. `start.bat` faylini ishga tushiring. Oyna ochiq turishi kerak.

Birinchi ishga tushganda agent faqat hozirgi holatni saqlaydi. Xatoliklar shundan keyingi o'zgarishlar bo'yicha yuboriladi.

## Qo'shimcha buyruqlar
`python main.py poll | digest | daily | weekly | absences` buyruqlari tekshiruvni yoki hisobotni darhol bajaradi.
`.env` da `DRY_RUN=1` qilsangiz, xabarlar Telegramga yuborilmaydi, faqat ekranga chiqadi.

## Cheklov
HolliHop API o'zgarishni aynan kim qilganini bermaydi. Shuning uchun xabarda guruhning **mas'ul xodimi** (Assignee) ko'rsatiladi.
