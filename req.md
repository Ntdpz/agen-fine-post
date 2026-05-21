✅ ทำได้ — นี่คือ Full Spec ฉบับอัปเดตสำหรับ Telegram + Hermes

---

## Architecture สุดท้าย

```
Telegram User
    ↓
Telegram Bot
    ↓
Hermes Agent (WSL)
    ├── ตีความคำถาม
    ├── แยกงาน Facebook / Google
    └── รวมผลลัพธ์เป็นคำตอบเดียว
    ↓
Collectors
    ├── Facebook Scraper (Playwright + persistent profile)
    │   ├── search keyword
    │   ├── wait_for_selector
    │   ├── scroll + load เพิ่ม
    │   └── ดึง post text / comment text / post link / comment link / image
    └── Google Collector
        ├── search keyword
        └── ดึง title / snippet / source / URL
    ↓
Clean Text + Metadata
    ↓
Hermes Formatter (via Ollama)
    ↓
Telegram Reply Only
```

> **หลักการ**: scraper หา data เอง, Hermes รับเฉพาะ clean text + metadata เพื่อสรุปและจัดรูปแบบคำตอบ  
> agent รันใน WSL และเรียก Ollama ผ่าน endpoint ฝั่ง Windows

---

## เป้าหมายของระบบ

ระบบนี้ต้องรับคำถามจาก Telegram เช่น:

```
หาข้อมูลใน Facebook ว่า ชัชชาติ คอมเมนต์ว่ายังไงบ้าง
หาข้อมูลใน Google ว่า ชัชชาติ ตอนนี้มีข้อมูลอะไรบ้าง
```

และตอบกลับเป็นแชท Telegram เท่านั้น โดยพยายามส่งข้อมูลต่อไปนี้เมื่อหาเจอ:

- ลิงก์โพสต์
- รูปจากโพสต์
- คอมเมนต์ที่เกี่ยวข้อง
- ลิงก์คอมเมนต์
- ลิงก์ข่าวหรือเว็บจาก Google
- สรุปภาพรวมแบบอ่านง่าย

---

## Stack ที่ใช้

| ชิ้นส่วน | เครื่องมือ | หมายเหตุ |
|---------|-----------|---------|
| Chat Interface | Telegram Bot API | รับคำถามและส่งคำตอบ |
| LLM | Hermes via Ollama | agent ใช้งานจาก WSL |
| Runtime | WSL Ubuntu | เรียก model ผ่าน `OLLAMA_HOST` |
| Browser Automation | Playwright + Chrome channel | ใช้กับ Facebook |
| Facebook Session | persistent profile | ใช้ session จริง |
| Search Source | Google | เก็บ title/snippet/url |
| Output | Telegram message only | ไม่ต้องมี web UI |

---

## เทคนิคหลักที่ต้องใช้: Playwright → Text → LLM

ส่วนนี้คือหัวใจของ demo และต้องส่งต่อให้ junior เข้าใจตรงกันทั้งทีม

แนวคิดหลักคือ **แบ่งงานให้ถูกคน**:

```text
Playwright  →  หาข้อมูล + รอให้หน้าโหลด + กด scroll + เก็บ link/image/comment
Hermes      →  สรุปผล + จัดรูปแบบ output ให้พร้อมส่งเข้า Telegram
```

สิ่งที่ห้ามทำคือส่ง HTML หรือ DOM ทั้งหน้า Facebook เข้า LLM ตรงๆ เพราะจะทำให้ context window เต็มเร็ว, ช้า, และได้ผลลัพธ์ไม่นิ่ง

สิ่งที่ต้องทำคือให้ Playwright ดึงออกมาเป็น `text` และ `metadata` ก่อน เช่น:

- post text
- post URL
- image URL
- comment text
- comment URL
- author
- title/snippet/url จาก Google

จากนั้นค่อยส่งข้อมูลที่สะอาดแล้วให้ Hermes จัดรูปแบบคำตอบ

---

## Step-by-Step สำหรับ Junior: ทำยังไงให้พร้อม demo

### Step 1: ให้ Telegram bot รับข้อความก่อน

เป้าหมายของ step นี้คือให้ระบบรับข้อความจากผู้ใช้ได้ 1 ข้อความ แล้วส่งต่อเข้า agent

ตัวอย่างข้อความ:

```text
ชัชชาติ คอมเมนต์ว่ายังไงบ้าง และตอนนี้ใน Google มีข้อมูลอะไรบ้าง
```

สิ่งที่ต้องทำ:

- รับข้อความจาก Telegram webhook หรือ polling
- เก็บ raw message เดิมไว้
- ส่งข้อความนั้นเข้า `agent.py`
- ยังไม่ต้อง parse ซับซ้อนในชั้น bot

หลักคิด:

- `telegram_bot.py` มีหน้าที่รับและส่งเท่านั้น
- logic การตีความคำถามอยู่ที่ `agent.py`

---

### Step 2: ให้ Hermes แยก intent ออกเป็นงานย่อย

เป้าหมายของ step นี้คือแปลงคำถามยาวๆ ให้เป็นแผนงานที่ deterministic มากขึ้น

ตัวอย่างสิ่งที่ agent ควรสรุปได้:

```json
{
  "keyword": "ชัชชาติ",
  "tasks": ["facebook", "google"],
  "facebook_goal": "หาคอมเมนต์และโพสต์ที่เกี่ยวข้อง",
  "google_goal": "หาข้อมูลล่าสุดจากผลค้นหา"
}
```

ข้อสำคัญ:

- Hermes ใน step นี้ทำหน้าที่ตีความคำถาม ไม่ได้อ่าน DOM
- output ของ step นี้ต้องสั้นและควบคุมได้
- ถ้าคำถามกำกวม ให้เลือก keyword เดียวก่อนใน MVP

---

### Step 3: Facebook scraper ต้องใช้ Playwright ดึง text ไม่ใช่ DOM

เป้าหมายของ step นี้คือเข้า Facebook search, รอให้ content โหลดจริง, แล้วดึงเฉพาะข้อมูลที่จำเป็น

ตัวอย่าง flow:

```python
await page.goto("https://www.facebook.com/search/posts?q=ชัชชาติ")
await page.wait_for_selector('[role="article"]', timeout=10000)
```

เหตุผลที่ใช้ `wait_for_selector('[role="article"]')`:

- Facebook โหลด content ผ่าน JavaScript
- ถ้ารีบอ่าน page เร็วเกินไป จะได้แค่โครง HTML
- ต้องรอจน post จริง render ลง DOM ก่อน

จากนั้นดึง text ของ post ออกมา:

```python
articles = page.locator('[role="article"]')
count = await articles.count()

for index in range(count):
  article = articles.nth(index)
  text = await article.inner_text()
```

สิ่งที่ junior ต้องเข้าใจ:

- `inner_text()` ได้ text ที่มองเห็นจริง
- ไม่ได้ HTML tag
- ไม่ได้ class ยาวๆ ที่รกและกิน token
- นี่คือจุดที่ลด context จากระดับหลายหมื่น token ลงมาเหลือระดับหลักร้อย

---

### Step 4: Scroll เพื่อโหลด posts เพิ่ม แล้วเก็บแบบกันซ้ำ

Facebook ไม่โหลดทุก post ตั้งแต่แรก จึงต้อง scroll และดึงข้อมูลเพิ่ม

ตัวอย่าง flow:

```python
posts = []
seen_texts = set()

for _ in range(5):
  articles = page.locator('[role="article"]')
  count = await articles.count()

  for index in range(count):
    article = articles.nth(index)
    text = await article.inner_text()
    if text and text not in seen_texts:
      seen_texts.add(text)
      posts.append(text)

  await page.keyboard.press("End")
  await page.wait_for_timeout(2000)
```

หลักคิด:

- ทุกครั้งที่ scroll จะมีทั้งของเก่าและของใหม่
- ต้องมี `seen_texts` หรือ key อื่นเพื่อกัน duplicate
- MVP ไม่ต้อง scroll เยอะ เช่น 3 ถึง 5 รอบพอ

---

### Step 5: เก็บ metadata ให้ครบตั้งแต่ชั้น scraper

อย่าดึงแค่ text อย่างเดียว เพราะโจทย์ต้องการส่งกลับ Telegram พร้อมลิงก์และรูป

สิ่งที่ Facebook scraper ควรพยายามเก็บต่อ 1 post:

- `author`
- `post_text`
- `post_url`
- `image_url`
- `comments[]`
- `comment_url`

หลักสำคัญ:

- scraper ต้องคืนข้อมูลเป็นโครงสร้าง ไม่ใช่ก้อน text เดียว
- ถ้าหา `comment_url` ไม่เจอ ให้คืนค่า `null` แล้วให้ formatter จัดการต่อ
- อย่ารอให้ LLM เดา link จาก text เพราะจะมั่ว

ตัวอย่างโครงสร้างที่ควรได้จาก scraper:

```json
{
  "author": "...",
  "post_text": "...",
  "post_url": "https://...",
  "image_url": "https://...",
  "comments": [
  {
    "author": "...",
    "text": "...",
    "comment_url": "https://..."
  }
  ]
}
```

---

### Step 6: Google collector ก็ต้องคืน clean text + metadata เหมือนกัน

เป้าหมายคือให้ข้อมูลจาก Facebook และ Google อยู่ในรูปแบบที่ใกล้กัน เพื่อให้ formatter รวมง่าย

สิ่งที่ต้องเก็บ:

- `title`
- `snippet`
- `source`
- `url`

สิ่งที่ไม่ควรทำ:

- อย่าส่ง HTML search result page เข้า Hermes
- อย่าให้ Hermes เป็นตัวหา URL เอง

---

### Step 7: ส่งให้ Hermes แค่ text และ metadata ที่จำเป็น

เมื่อ scraper และ collector ทำงานเสร็จแล้ว ให้สร้าง prompt สั้นๆ สำหรับ Hermes

หน้าที่ของ Hermes มีแค่:

- รวมข้อมูล Facebook + Google
- สรุปเนื้อหาให้อ่านง่าย
- จัดรูปแบบให้เหมาะกับ Telegram
- ไม่ต้องวิเคราะห์จาก DOM

ตัวอย่าง prompt แนวที่ควรใช้:

```text
สรุปข้อมูลต่อไปนี้เป็นข้อความสำหรับ Telegram

เงื่อนไข:
- ตอบเป็นภาษาไทย
- แบ่งส่วน Facebook และ Google ชัดเจน
- ถ้าไม่มี comment_url ให้เขียนว่า "ไม่มีลิงก์คอมเมนต์"
- อย่าสร้างข้อมูลที่ไม่มีใน input

Input:
{facebook_results_json}
{google_results_json}
```

หลักคิด:

- prompt ต้องบอกข้อห้ามชัด เช่น `อย่าสร้างข้อมูลที่ไม่มีใน input`
- input ควรเป็น JSON หรือ structured text ไม่ใช่ข้อความมั่วๆ ปนกัน
- ยิ่ง input สะอาด ผลลัพธ์ยิ่งนิ่ง

---

### Step 8: Formatter ต้องตรวจ output ก่อนส่ง Telegram

อย่าเชื่อ LLM 100% แม้จะใช้แค่ formatting ก็ยังมีโอกาสหลุดรูปแบบ

สิ่งที่ต้องตรวจ:

- มีหัวข้อ Facebook และ Google หรือไม่
- URL ที่ควรมีถูกใส่มาหรือไม่
- ข้อความยาวเกิน limit ของ Telegram หรือไม่
- มี field ที่ LLM แต่งเพิ่มเองหรือไม่

ถ้า output ยาวเกินไป:

- ตัดเหลือ top 2 Facebook posts และ top 3 Google results
- หรือแบ่งเป็นหลายข้อความ

---

## โครง pipeline ที่ junior ควรทำตาม

```text
telegram_bot.py
  ↓ รับข้อความ user
agent.py
  ↓ ใช้ Hermes สร้าง search plan
facebook_scraper.py
  ↓ คืน facebook_results เป็น list ของ dict
google_collector.py
  ↓ คืน google_results เป็น list ของ dict
formatter.py
  ↓ ใช้ Hermes สรุป + format เป็น Telegram text
telegram_bot.py
  ↓ ส่งข้อความกลับ
```

หลักสำคัญของ pipeline นี้:

- scraper หา facts
- formatter เรียบเรียง facts
- Telegram bot ส่งออก
- อย่าเอา role เหล่านี้มาปนกัน

---

## Checklist สำหรับลงมือทำ demo

ก่อนเริ่มเขียนโค้ด junior ต้องทำ checklist นี้ให้ครบ:

- มี Telegram bot token แล้ว
- WSL Ubuntu รันได้
- Ollama ฝั่ง Windows รันอยู่
- ใน WSL ตั้ง `OLLAMA_HOST` ให้ชี้ไปฝั่ง Windows แล้ว
- Chrome profile ที่จะใช้กับ Facebook พร้อม
- Playwright ใช้งานได้
- login Facebook ใน profile นั้นเรียบร้อย
- ทดสอบเปิด Facebook search ด้วย Playwright ได้จริง
- ทดสอบเรียก Hermes ได้จริงจาก WSL

ตัวอย่าง environment ที่ควรมี:

```text
OLLAMA_HOST=http://172.18.16.1:11434
OLLAMA_MODEL=hermes
TELEGRAM_BOT_TOKEN=...
FACEBOOK_PROFILE_DIR=...
```

ถ้า runtime จริงจะใช้ model ชื่ออื่นใน Ollama ก็เปลี่ยนได้ แต่เอกสารฉบับนี้ถือว่า orchestration layer ใช้ Hermes ตาม requirement ปัจจุบัน

---

## สิ่งที่ junior มักพลาด

- ส่ง `page.content()` เข้า LLM ตรงๆ
- ใช้ class selector ของ Facebook ที่เปลี่ยนบ่อย
- ไม่รอ page โหลดแล้วรีบ scrape
- scroll แล้วไม่กัน duplicate
- ให้ LLM เดา URL เองแทนการเก็บจาก scraper
- ส่งผลลัพธ์ยาวเกินจน Telegram ตัดข้อความ
- ไม่เผื่อกรณี `comment_url` หาไม่ได้

ถ้าเจอปัญหา selector เปลี่ยน ให้แก้ที่ scraper ก่อน ไม่ใช่ไปเพิ่ม prompt ให้ Hermes เดาแทน

---

## Definition of Done สำหรับ demo รอบแรก

งานถือว่าเสร็จเมื่อทำครบทุกข้อ:

- user ส่งข้อความหา bot ใน Telegram ได้
- bot ตอบกลับได้ภายใน flow เดียว
- มีผลลัพธ์ Facebook อย่างน้อย 1 ถึง 3 posts
- มีผลลัพธ์ Google อย่างน้อย 3 results
- คำตอบมีลิงก์โพสต์
- ถ้ามีรูป ต้องมี image URL ในข้อความ
- ถ้ามีคอมเมนต์ ต้องแสดง comment text อย่างน้อย 1 รายการต่อ post ที่เลือก
- ถ้าหา comment link ไม่เจอ ต้องตอบแบบ explicit ว่าไม่มี
- Hermes ใช้สรุปและ format เท่านั้น ไม่ได้อ่าน DOM ตรงๆ

---

## MVP Scope

MVP รอบแรกควรทำเฉพาะสิ่งที่จำเป็นต่อ demo:

- รับข้อความจาก Telegram 1 ข้อความต่อ 1 คำถาม
- ให้ Hermes ตีความคำถามและสร้าง keyword เดียว
- ค้น Facebook เฉพาะ posts ที่เกี่ยวข้องกับ keyword
- ดึงเฉพาะ post text, post URL, image URL และ comments ที่มองเห็นได้
- ค้น Google และดึงเฉพาะ title, snippet, source, URL
- รวมผลลัพธ์เป็นข้อความเดียวและตอบกลับใน Telegram
- จำกัดจำนวนผลลัพธ์ เช่น Facebook 3 posts และ Google 5 results
- ไม่ทำ multi-turn memory
- ไม่ทำฐานข้อมูล
- ไม่ทำ scheduling หรือ monitoring

---

## Future Scope

สิ่งต่อไปนี้ควรเลื่อนไปหลัง demo:

- รองรับหลาย keyword ในคำถามเดียว
- แยก intent หลายแบบ เช่น สรุป sentiment, จัดลำดับความสำคัญ, เปรียบเทียบหลายคน
- เก็บผลลัพธ์ลงฐานข้อมูลเพื่อค้นย้อนหลัง
- รองรับแนบรูปเข้า Telegram เป็น media message จริง ไม่ใช่ลิงก์อย่างเดียว
- ทำ deduplication ของ posts/comments/news
- ทำ retry workflow เมื่อ Facebook โหลดไม่ครบ
- เพิ่ม validation ว่า comment link เปิดได้จริง
- ทำ admin command ใน Telegram เช่น `/health`, `/rerun`, `/debug`

---

## Demo Flow

**Input จาก Telegram:**
```
ชัชชาติ คอมเมนต์ว่ายังไงบ้าง และตอนนี้ใน Google มีข้อมูลอะไรบ้าง
```

**Flow จริง:**
```
1. Telegram bot รับข้อความจาก user
2. Hermes แยกเป็น 2 งาน: Facebook และ Google
3. Facebook scraper เปิด search และดึง:
   - post text
   - post URL
   - image URL
   - comments ที่มองเห็นได้
   - comment URL ถ้าหาได้
4. Google collector ดึง:
   - title
   - snippet
   - source
   - URL
5. Hermes รวมผลและสรุปเป็นข้อความเดียว
6. ส่งกลับไปใน Telegram เท่านั้น
```

---

## รูปแบบคำตอบใน Telegram

```text
สรุปข้อมูลเกี่ยวกับ ชัชชาติ

Facebook
1. โพสต์: [หัวข้อหรือข้อความย่อ]
ลิงก์โพสต์: https://...
รูป: https://...
คอมเมนต์ที่พบ: "..."
ลิงก์คอมเมนต์: https://...

2. โพสต์: [หัวข้อหรือข้อความย่อ]
ลิงก์โพสต์: https://...
รูป: https://...
คอมเมนต์ที่พบ: "..."
ลิงก์คอมเมนต์: https://...

Google
1. [ชื่อข่าวหรือหน้าเว็บ]
แหล่งที่มา: ...
ลิงก์: https://...
สรุป: ...

2. [ชื่อข่าวหรือหน้าเว็บ]
แหล่งที่มา: ...
ลิงก์: https://...
สรุป: ...
```

> Output ต้องเป็นข้อความสำหรับ Telegram เป็นหลัก  
> ถ้ามีรูปหรือ URL ให้แนบเป็นลิงก์ในข้อความได้

---

## Code Structure

```
demo-agent-scrape/
├── main.py              ← entry point ของ bot
├── telegram_bot.py      ← รับ/ส่งข้อความกับ Telegram
├── agent.py             ← Hermes orchestration
├── browser.py           ← Playwright persistent context
├── facebook_scraper.py  ← ค้น Facebook, ดึง post/comment/link/image
├── google_collector.py  ← ค้น Google, ดึง title/snippet/url
├── formatter.py         ← รวมผลและสร้างข้อความตอบกลับ Telegram
├── schemas.py           ← schema ของ post/comment/search result
└── requirements.txt
```

---

## Data Schema ที่ต้องการ

**Facebook Result**
```json
{
  "platform": "facebook",
  "author": "...",
  "post_text": "...",
  "post_url": "https://facebook.com/...",
  "image_url": "https://...",
  "comments": [
    {
      "author": "...",
      "text": "...",
      "comment_url": "https://facebook.com/..."
    }
  ]
}
```

**Google Result**
```json
{
  "platform": "google",
  "title": "...",
  "snippet": "...",
  "source": "...",
  "url": "https://..."
}
```

---

## ข้อจำกัดที่ยังมีอยู่ (ต้องรู้ก่อน build)

| ข้อจำกัด | ระดับความเสี่ยง |
|---------|---------------|
| ToS Facebook/IG/X | 🔴 เสี่ยงสูง โดยเฉพาะการ scrape comments |
| Comment link บางอันอาจหาไม่ได้หรือเปิดไม่ได้ | 🟡 ต้องรองรับกรณี missing |
| Facebook UI เปลี่ยนบ่อย | 🔴 selector พังได้ง่าย |
| Google scraping เปราะบางถ้าไม่ใช้ API | 🟡 ต้องเตรียม fallback |
| Hermes local อาจสรุปผิดหรือ format หลุด | 🟡 ต้องมี retry + validation |
| Telegram reply อาจยาวเกิน limit | 🟡 ต้องตัดตอนหรือแบ่งหลายข้อความ |

---

**Reasoned conclusion:**

Demo นี้ออกแบบได้จริงในเชิงสถาปัตยกรรม โดยใช้ Telegram เป็น front door, Hermes เป็นตัว orchestration และสรุปผล, Playwright เป็นตัวเก็บข้อมูลจาก Facebook, และ Google collector เป็นอีกแหล่งข้อมูลหนึ่ง

สำหรับรอบแรกควรทำตาม **MVP Scope** เท่านั้น เพื่อให้ demo ออกมาไวและลดความเสี่ยงจาก Facebook UI, comment links, และข้อความ Telegram ที่ยาวเกินไป