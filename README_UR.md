# All-in-One YouTube Shorts Automation (MVP)

یہ ایک قابلِ چلنے والا ابتدائی full-stack پروجیکٹ ہے:
- Responsive browser dashboard (موبائل، لیپ ٹاپ، کمپیوٹر)
- تصاویر اپلوڈ کرنا (چھ تصاویر منتخب کریں)
- FFmpeg سے عمودی MP4 slideshow بنانا، ہر تصویر 2 سیکنڈ
- سادہ موضوع کے مطابق title/description/tags suggestions
- Google OAuth کے ذریعے متعدد YouTube اکاؤنٹس/چینلز جوڑنے کی بنیاد
- فوری اپلوڈ اور scheduled upload
- SQLite job history

## ضروریات
- Python 3.11+
- FFmpeg اور ffprobe انسٹال اور PATH میں
- Google Cloud project، YouTube Data API v3 enabled
- OAuth 2.0 Web application credentials JSON

## مقامی طور پر چلائیں
1. ZIP extract کریں اور اس فولڈر میں terminal کھولیں۔
2. virtual environment بنائیں:
   - Windows: `py -m venv .venv` پھر `.venv\\Scripts\\activate`
   - macOS/Linux: `python3 -m venv .venv` پھر `source .venv/bin/activate`
3. `pip install -r requirements.txt`
4. `.env.example` کو `.env` کاپی کریں۔
5. `.env` میں:
   - `SESSION_SECRET` کو ایک لمبی random string سے بدلیں۔
   - `FERNET_KEY` کے لیے یہ کمانڈ چلائیں: `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`
   - `GOOGLE_CLIENT_SECRETS_FILE=client_secret.json` رکھیں۔
6. Google Cloud Console میں YouTube Data API v3 فعال کریں اور OAuth Web client بنائیں۔ Authorized redirect URI بالکل `http://localhost:8000/oauth/callback` ہو۔ downloaded client secrets کو پروجیکٹ میں `client_secret.json` کے نام سے رکھیں۔ اسے کسی کے ساتھ share نہ کریں۔
7. `uvicorn app:app --host 127.0.0.1 --port 8000` چلائیں۔
8. براؤزر میں `http://127.0.0.1:8000` کھولیں۔

## خودکار آن لائن تصاویر (نیا)
1. Pexels Developers پر API key بنائیں: https://www.pexels.com/api/ ۔
2. `.env` میں `PEXELS_API_KEY` شامل کریں۔
3. ڈیش بورڈ میں موضوع لکھ کر `موضوع سے خودکار تصاویر` استعمال کریں؛ ایپ Pexels API سے چھ تصاویر تلاش کرکے ویڈیو بنائے گی۔
4. یہ Pexels کی تصاویر استعمال کرتا ہے، Pinterest سے scraping نہیں کرتا۔ Pexels API کی شرائط کے مطابق Pexels کا نمایاں لنک اور جہاں ممکن ہو فوٹوگرافر کریڈٹ دکھایا جائے۔
5. `Ronaldo` سرچ سے اصل Cristiano Ronaldo کی تصاویر ملنے کی ضمانت نہیں؛ API عام طور پر دستیاب لائسنس یافتہ سرچ نتائج دے گا۔ اگر کسی مخصوص شخص/تصویر کے حقوق واضح نہ ہوں تو اسے استعمال نہ کریں۔

## استعمال
1. Add YouTube channel پر جا کر Google سے اجازت دیں۔ ہر اضافی چینل کے لیے الگ اکاؤنٹ connect کریں۔
2. 6 تصاویر منتخب کریں؛ موضوع (Islamic, Ronaldo/Football, Other) منتخب کریں۔
3. Generate video دبائیں۔ FFmpeg ضروری ہے۔
4. تیار ویڈیو کا preview/download کریں۔
5. چینل منتخب کریں، عنوان/تفصیل/ٹیگز دیکھیں اور Upload now یا Schedule upload استعمال کریں۔

## کلاؤڈ پر چلانا
اسی کوڈ کو HTTPS والی Python hosting پر چلایا جا سکتا ہے۔ وہاں environment variables set کریں، OAuth redirect URI کو اپنی HTTPS domain کے ساتھ بدلیں، اور persistent disk استعمال کریں۔ SQLite/media فولڈر کو ephemeral filesystem پر نہ رکھیں۔ Production میں database migrations, access control, encrypted backups, rate limiting, audit logs اور per-user authentication شامل کریں۔

## اہم حدود اور تحفظ
- یہ MVP ہے، production-ready multi-user SaaS نہیں۔
- `FERNET_KEY` اور session secret خفیہ رکھیں؛ `.env`/`client_secret.json` GitHub پر upload نہ کریں۔
- OAuth consent، API quota اور YouTube کی policies لاگو ہوں گی۔ Unverified API projects کے upload visibility پر restrictions ہو سکتی ہیں؛ پہلے private upload سے test کریں۔
- خودکار طور پر کسی بھی trending song کو استعمال نہیں کرتا۔ صرف وہ آڈیو استعمال کریں جس کے حقوق/اجازت آپ کے پاس ہو۔ Shorts کی official audio library سے منتخب آڈیو کو الگ workflow میں لگانا پڑ سکتا ہے۔
- خودکار schedule صرف اسی وقت چلتا ہے جب سرور آن ہو۔ فون/لیپ ٹاپ بند ہونے کے باوجود schedule چلانے کے لیے مستقل cloud server چاہیے۔
- یہ ایپ ویڈیو کو 9:16 میں بناتی ہے، تصاویر کو fit کرتی ہے اور ہلکی fade transitions لگاتی ہے۔ نتیجہ upload سے پہلے ضرور preview کریں۔
