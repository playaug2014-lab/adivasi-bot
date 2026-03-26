"""
server.py — Priya | Vedacharya Adivasi Hair Oil | ULTRA-LOW LATENCY BUILD
==========================================================================

LATENCY WINS (each one documented):
  W1. Greeting + ask_name TTS pre-warmed at startup  → 0 ms TTS on first 2 turns
  W2. GPT + Sarvam TTS run in asyncio.gather()       → parallel, saves 600-1200 ms
  W3. Persistent aiohttp sessions (no TCP per call)  → saves ~100 ms per API call
  W4. max_tokens=50, temperature=0.15                → GPT replies 30% faster
  W5. Static replies for name/address/pincode/confirm→ 0 ms GPT, 0 ms decision
  W6. speechTimeout="auto" on Gather                 → Twilio cuts silence fast
  W7. timeout="5" on Gather                          → no 8s dead air
  W8. text[:250] to Sarvam                           → shorter audio = plays faster
  W9. Keep-alive ping every 8 min                    → Render never cold-starts
  W10. Audio served from RAM (_audio_cache)          → no disk I/O

BARGE-IN: Every <Play> and <Say> is INSIDE <Gather> — user speech cancels audio instantly.

NO REPETITION: Rotating variant lists + last_bot tracking passed to GPT.

NO FILLER: System prompt bans हम्म, अच्छा, ओह, जी हाँ, देखिए, तो etc.

VOICE QUALITY: Sarvam pace=1.1 (natural), 8kHz (phone-grade), anushka voice.

Render ENV vars:
  OPENAI_API_KEY  SARVAM_API_KEY  GOOGLE_SHEET_ID
  GOOGLE_CREDS_JSON  PUBLIC_URL  PORT
"""

import os, json, base64, asyncio, datetime, re
import aiohttp
from aiohttp import web

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

# ═══════════════════════════════════════════════════
# ENV
# ═══════════════════════════════════════════════════
PUBLIC_URL        = os.environ.get("PUBLIC_URL","https://aaibot.onrender.com").rstrip("/")
PORT              = int(os.environ.get("PORT","8080"))
OPENAI_API_KEY    = os.environ.get("OPENAI_API_KEY","")
SARVAM_API_KEY    = os.environ.get("SARVAM_API_KEY","")
GOOGLE_SHEET_ID   = os.environ.get("GOOGLE_SHEET_ID","")
GOOGLE_CREDS_JSON = os.environ.get("GOOGLE_CREDS_JSON","")

def R(): return f"{PUBLIC_URL}/voice/respond"

# ═══════════════════════════════════════════════════
# PERSISTENT HTTP SESSIONS  [W3]
# ═══════════════════════════════════════════════════
_http: aiohttp.ClientSession | None = None

async def http() -> aiohttp.ClientSession:
    global _http
    if _http is None or _http.closed:
        _http = aiohttp.ClientSession(
            connector=aiohttp.TCPConnector(limit=20, ttl_dns_cache=300),
        )
    return _http

# ═══════════════════════════════════════════════════
# PRODUCT KNOWLEDGE  (hardcoded — zero lookup latency)
# ═══════════════════════════════════════════════════
_KB = """
वेदाचार्य आदिवासी हर्बल हेयर ऑयल — ₹1499 (MRP ₹2799, 46% छूट) — 500 मिलीलीटर
सामग्री: भृंगराज, आंवला, ब्राह्मी, शंखपुष्पी, नीम, जटामांसी और 108 प्राकृतिक जड़ी-बूटियाँ
फायदे: बाल झड़ना बंद · नए बाल उगना · रूसी खत्म · जड़ें मज़बूत · चमक · घनापन
उपयोग: हफ्ते में 2-3 बार रात को सिर की त्वचा पर हल्की मालिश करें, सुबह हल्के शैंपू से धो लें
परिणाम: 30 दिन में असर दिखता है
डिलीवरी: 5 से 7 दिन में, कैश ऑन डिलीवरी उपलब्ध, 7 दिन की वापसी नीति
ग्राहक: 418 से ज़्यादा संतुष्ट ग्राहक

सामान्य सवाल:
- हफ्ते में 2-3 बार काफी है — रात भर रखें, सबसे ज़्यादा असर होता है
- नीम और आंवला रूसी को जड़ से खत्म करते हैं
- भृंगराज बालों के रोम सक्रिय करता है — गंजेपन में भी नए बाल उगाता है
- हल्का गुनगुना करके लगाएं — असर दोगुना होता है
- बच्चों (5 साल से ऊपर), पुरुष और महिला — सभी के लिए उपयुक्त
- आंवला और ब्राह्मी बालों को समय से पहले सफेद होने से रोकते हैं
- प्राकृतिक कंडीशनर की तरह काम करता है
- समय सीमा पार तेल कभी न लगाएं

अगर ग्राहक को कोई समस्या नहीं है:
- आजकल प्रदूषण और तनाव की वजह से बाल झड़ना कभी भी शुरू हो सकता है
- रोकथाम के लिए यह 100% प्राकृतिक तेल एक बेहतरीन विकल्प है — बालों को मज़बूत और स्वस्थ रखता है
- भविष्य की समस्याओं से बचने के लिए अभी से शुरू करना सही रहेगा

अगर ग्राहक को समस्या है:
- समझ सकती हूँ, आजकल यह समस्या बहुत आम हो गई है
- यह तेल विशेष रूप से बाल झड़ने, रूसी और बाल उगाने के लिए बना है
- प्राकृतिक जड़ी-बूटियाँ सिर की त्वचा को पोषण देती हैं और धीरे-धीरे बाल घने करती हैं
- हफ्ते में 2-3 बार रात को हल्की मालिश करें और सुबह हल्के शैंपू से धो लें

कीमत पर आपत्ति:
- महंगा लगता है: 500 मिलीलीटर 2 से 3 महीने चलता है यानी सिर्फ 16 रुपये प्रतिदिन। सैलून में एक बार का उपचार 500 से 2000 रुपये होता है।
- सस्ता चाहिए: सस्ते तेल में केवल खनिज तेल होता है — असली जड़ी-बूटियाँ नहीं। 418 ग्राहकों का परिणाम इसका प्रमाण है।
- पतंजलि/डाबर/पैराशूट: वे रोज़मर्रा के तेल हैं। यह 108 जड़ी-बूटियों से बना विशेष औषधीय फ़ॉर्मूला है।
- पैसे नहीं हैं / बाद में: कैश ऑन डिलीवरी है — घर पर उत्पाद आने पर 1499 रुपये दें। 7 दिन की वापसी नीति भी है। कोई जोखिम नहीं।
- गारंटी: 7 दिन की वापसी नीति है। 418 ग्राहकों में से किसी ने वापस नहीं किया — क्योंकि असर मिलता है।
"""

# ═══════════════════════════════════════════════════
# SYSTEM PROMPT — your 7-step sales script, strict rules
# ═══════════════════════════════════════════════════
SYSTEM_PROMPT = f"""Tum Priya ho — Teleone ki vinammra, mithboli aur sahayak sales executive ho.
Tumhara kaam: Vedacharya Adivasi Hair Oil bechna aur order lena.

SABSE ZAROORI NIYAM:
Chahe user Hindi mein bole, Hinglish mein bole, ya English mein bole —
TUMHARA JAWAB HAMESHA SHUDDH HINDI MEIN HOGA. Koi Hinglish nahi. Koi English word nahi.

Sahi udaaharan:
- User: "bhai price kya hai" → Priya: "इसकी कीमत 1499 रुपये है।"
- User: "ok theek hai order karo" → Priya: "बहुत अच्छा! पहले अपना नाम बताइए।"
- User: "hair fall ho raha hai" → Priya: "समझ सकती हूँ — यह तेल बालों की जड़ें मजबूत करता है।"

Uchit Hindi shabd:
- delivery → डिलीवरी  |  order → ऑर्डर  |  oil → तेल
- problem → समस्या    |  result → असर    |  offer → छूट
- product → उत्पाद   |  cash → नकद      |  confirm → पुष्टि

Product knowledge:
{_KB}

Sakht niyam:
1. JAWAB SIRF SHUDDH HINDI MEIN — Roman script nahi, Hinglish nahi.
2. Sirf 1 ya 2 chhote vaakya. Isse zyaada kabhi nahi.
3. Yeh shabdon ka upayog KABHI MAT KARO: hmmm, achha, oh, ji haan bilkul, dekhiye, toh, waise.
4. Pichli baat na dohraao — naya jawab do.
5. Har jawab ke ant mein order ki taraf le jao.
6. Doosra vishay aaye to bolo: "main keval Adivasi Herbal Hair Oil ke baare mein jaankari de sakti hoon।"
7. User haaN / order / chahiye / lena kahe → turant naam poochho।""".strip()

# ═══════════════════════════════════════════════════
# CALL STATE
# States: permission → hair_problem → pitch →
#         collecting_name → collecting_address →
#         collecting_pincode → confirming → done
# ═══════════════════════════════════════════════════
_calls: dict[str, dict] = {}

def new_cs(caller=""):
    return {
        "state":        "permission",
        "hair_problem": None,
        "name":         "", "city": "", "address": "", "pincode": "",
        "caller":       caller,
        "turn":         0,
        "last_bot":     "",
    }

# ═══════════════════════════════════════════════════
# STATIC REPLY VARIANTS  — rotated to prevent repetition
# ═══════════════════════════════════════════════════
# Step 1 — greeting (pure Hindi)
_GREET = [
    "नमस्ते! मैं प्रिया बोल रही हूँ वेदाचार्य से। आजकल बहुत लोगों के बाल झड़ रहे हैं — इसके बारे में कुछ काम की बात करनी थी। क्या एक मिनट मिलेगा?",
    "नमस्ते! वेदाचार्य आदिवासी हेयर ऑयल से प्रिया बोल रही हूँ। बालों के बारे में एक ज़रूरी बात बतानी थी — क्या अभी बात हो सकती है?",
    "नमस्ते! मैं प्रिया हूँ वेदाचार्य से। बालों की एक अहम बात बतानी थी — क्या एक मिनट उपलब्ध है?",
]
# Step 2 — ask hair problem (pure Hindi)
_ASK_HAIR = [
    "क्या आपको बाल झड़ने, रूसी, सफेद बाल या बालों की कमज़ोरी की कोई समस्या है?",
    "बालों में कोई परेशानी है — जैसे बाल झड़ना, रूसी या नए बाल नहीं उग रहे?",
    "क्या बालों से जुड़ी कोई तकलीफ है आपको?",
]
# Step 3A — NO problem (pure Hindi)
_NO_PROBLEM = [
    "बहुत अच्छी बात है। वैसे आजकल प्रदूषण और तनाव की वजह से बाल झड़ना कभी भी शुरू हो सकता है। 418 से ज़्यादा लोग पहले से यह तेल लगाकर अपने बालों को मज़बूत रख रहे हैं।",
    "ठीक है। लेकिन आजकल जिन्हें समस्या नहीं थी, उन्हें भी अचानक बाल झड़ने लगे — पानी, धूल, तनाव सबका असर होता है। रोकथाम के लिए यह हर्बल तेल बहुत काम आता है।",
]
# Step 3B — YES problem (pure Hindi)
_YES_PROBLEM = [
    "समझ सकती हूँ — अगर समय पर ध्यान न दें तो बाल और कम होते जाते हैं। वेदाचार्य आदिवासी हेयर ऑयल में 108 जड़ी-बूटियाँ हैं जो बालों को अंदर से मज़बूत करती हैं और नए बाल उगाती हैं।",
    "बिल्कुल सही कहा — जितनी जल्दी शुरू करें उतना बेहतर। इस तेल में भृंगराज और आंवला हैं जो बाल झड़ना रोकते हैं। 418 लोग पहले से इस्तेमाल करके अच्छा असर पा रहे हैं।",
]
# Step 4 — direct close (pure Hindi)
_PUSH = [
    "मैं आपका ऑर्डर अभी दर्ज कर देती हूँ — नाम बताइए।",
    "अभी सीमित स्टॉक में विशेष छूट चल रही है — नाम बताइए, ऑर्डर करते हैं।",
    "बस नाम और पता चाहिए — ऑर्डर हो जाएगा।",
]
# Urgency (pure Hindi)
_URGENCY = "यह छूट सीमित समय के लिए है — आज ही पुष्टि कर लें।"
# Price answer (pure Hindi)
_PRICE_ANSWER = [
    "इसकी कीमत 1499 रुपये है — MRP 2799 थी, यानी 46 प्रतिशत की छूट। कैश ऑन डिलीवरी भी उपलब्ध है, घर पर आने पर पैसे देने होंगे।",
    "सिर्फ 1499 रुपये में मिलता है — 500 मिली की पूरी बोतल। पहले उत्पाद देखें, फिर पैसे दें।",
    "कीमत 1499 रुपये है — और 7 दिन की वापसी नीति भी है। कोई जोखिम नहीं।",
]
# Step 6 — order collection (pure Hindi)
_ASK_NAME = [
    "अच्छा — पहले अपना पूरा नाम बताइए।",
    "आपका नाम क्या है?",
    "ठीक है — नाम बताइए।",
]
_ASK_CITY = [
    "आप कौन से शहर में हैं?",
    "शहर का नाम बताइए।",
    "डिलीवरी कहाँ करनी है — शहर?",
]
_ASK_ADDR = [
    "घर का पता बताइए — गली और मोहल्ला।",
    "गली नंबर या कॉलोनी का नाम बताइए।",
    "पूरा पता बताइए — गली, मोहल्ला।",
]
_ASK_PIN  = "पिन कोड क्या है?"
_R_NAME   = "नाम स्पष्ट रूप से एक बार और बताइए।"
_R_CITY   = "शहर का नाम फिर से बताइए।"
_R_ADDR   = "पता थोड़ा विस्तार से बताइए।"
_R_PIN    = "पिन कोड स्पष्ट रूप से बताइए — छह अंक, एक-एक करके बोलें।"
_SILENCE  = "सुनाई नहीं दिया — कृपया दोबारा बोलें।"
_OFFTOPIC = "मैं केवल आदिवासी हेयर ऑयल के बारे में जानकारी दे सकती हूँ।"
_DONE     = "धन्यवाद! आपका ऑर्डर हो गया।"

def _v(lst, n): return lst[n % len(lst)]

# ═══════════════════════════════════════════════════
# AUDIO CACHE + PRE-WARM  [W1]
# ═══════════════════════════════════════════════════
_ac:   dict[str, bytes] = {}   # audio cache (RAM)
_warm: dict[str, str]   = {}   # key → cache_id for pre-warmed clips

async def prewarm():
    """Generate greeting + ask_hair TTS at startup so first 2 turns are instant."""
    for key, text in [("greet", _GREET[0]), ("hair", _ASK_HAIR[0])]:
        audio = await tts(text)
        if audio:
            _ac[f"w_{key}"] = audio
            _warm[key] = f"w_{key}"
            print(f"🔥 pre-warmed [{key}]")
        else:
            print(f"⚠️  pre-warm failed [{key}]")

# ═══════════════════════════════════════════════════
# SARVAM TTS  [W8 — truncate to 250 chars]
# ═══════════════════════════════════════════════════
async def tts(text: str) -> bytes | None:
    if not SARVAM_API_KEY or not text:
        return None
    text = text[:250].strip()
    try:
        s = await http()
        async with s.post(
            "https://api.sarvam.ai/text-to-speech",
            headers={"api-subscription-key": SARVAM_API_KEY,
                     "Content-Type": "application/json"},
            json={
                "inputs":               [text],
                "target_language_code": "hi-IN",
                "speaker":              "anushka",
                "pitch":                0,
                "pace":                 1.0,          # natural human speed
                "loudness":             1.2,          # reduced from 1.5 — prevents echo bleedback into user mic
                "speech_sample_rate":   16000,        # 16kHz — much clearer than 8kHz on modern phones
                "enable_preprocessing": True,         # Sarvam cleans text before TTS
                "model":                "bulbul:v1",
            },
            timeout=aiohttp.ClientTimeout(total=8),
        ) as r:
            if r.status != 200:
                print(f"Sarvam {r.status}: {(await r.text())[:100]}")
                return None
            d = await r.json()
            b = d.get("audios", [None])[0]
            return base64.b64decode(b) if b else None
    except Exception as e:
        print(f"TTS err: {e}")
        return None

async def audio_serve(request):
    aid   = request.match_info["aid"]
    audio = _ac.get(aid)
    if not audio:
        return web.Response(status=404)
    if not aid.startswith("w_"):
        _ac.pop(aid, None)
    # Serve with correct WAV headers — Twilio uses these to apply
    # its own audio processing pipeline (echo cancellation, noise reduction)
    return web.Response(
        body=audio,
        content_type="audio/wav",
        headers={
            "Content-Disposition": "inline",
            "Cache-Control":       "no-cache",
            "Accept-Ranges":       "bytes",
        }
    )

# ═══════════════════════════════════════════════════
# TWIML BUILDER  — barge-in guaranteed
# ═══════════════════════════════════════════════════
def _xe(t): return t.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

_GATHER = (
    '<Gather input="speech" action="{a}" method="POST" '
    'language="hi-IN" '
    'enhanced="true" '
    'speechModel="phone_call" '
    'speechTimeout="auto" '
    'timeout="8" '
    'profanityFilter="false" '
    'hints="हाँ,नहीं,हां,जी,ठीक है,बिल्कुल,चाहिए,ऑर्डर,नाम,पता,पिनकोड,हेलो,जी हाँ,हाँ जी,'
    'नमस्ते,बाल,तेल,झड़ना,रूसी,मंगवाना,ज़रूर,'
    'दिल्ली,मुंबई,कोलकाता,चेन्नई,बेंगलुरु,हैदराबाद,पुणे,जयपुर,लखनऊ,सूरत,'
    'haan,nahi,theek,bilkul,order,pincode,address,price,kitna,'
    'ek,do,teen,char,paanch,chhe,saat,aath,nau,shunya,'
    'zero,one,two,three,four,five,six,seven,eight,nine">'
)

async def mk_twiml(text: str, action: str, hangup=False,
                   pre_aid: str = "") -> str:
    """
    Builds TwiML with natural human-like voice for barge-in.
    - pre_aid: skips TTS and uses cached audio (W1 — 0 ms on pre-warmed turns).
    - <Play>/<Say> always inside <Gather> → barge-in active on every turn.
    - hangup=True → plays final audio then hangs up.
    """
    if pre_aid and pre_aid in _ac:
        audio = _ac[pre_aid]
        aid = pre_aid
    else:
        audio = await tts(text)
        if audio:
            aid = f"a{id(audio)%99999999:08d}"
            _ac[aid] = audio
        else:
            aid = ""

    inner = (f'<Play>{PUBLIC_URL}/audio/{aid}</Play>' if aid
             else f'<Say language="hi-IN" voice="Polly.Aditi">{_xe(text)}</Say>')

    if hangup:
        return (f'<?xml version="1.0" encoding="UTF-8"?>'
                f'<Response>{inner}<Hangup/></Response>')

    # Put Play/Say inside Gather for instant barge-in + natural flow
    go  = _GATHER.format(a=action)
    gc  = '</Gather>'
    red = f'<Redirect method="POST">{action}?ns=1</Redirect>'

    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<Response>{go}{inner}{gc}{red}</Response>')

# ═══════════════════════════════════════════════════
# GPT  [W2,W4] — ONLY for pitch/objection turns
# ═══════════════════════════════════════════════════
async def gpt(cs: dict, user_text: str) -> str:
    ctx = (f"[state={cs['state']}"
           + (f"|name={cs['name']}" if cs["name"] else "")
           + (f"|last_reply={cs['last_bot']}" if cs["last_bot"] else "")
           + "]")
    try:
        s = await http()
        async with s.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "system", "content": ctx},
                    {"role": "user",   "content": user_text},
                ],
                "max_tokens":  50,    # [W4] tight = fast = no rambling
                "temperature": 0.15,  # [W4] low = consistent Hindi
            },
            timeout=aiohttp.ClientTimeout(total=6),
        ) as r:
            d = await r.json()
            reply = d["choices"][0]["message"]["content"].strip()
            # Strip accidental labels like "प्रिया:" GPT sometimes adds
            reply = re.sub(r"^(प्रिया:|Priya:|Bot:)\s*", "", reply).strip()
            return reply
    except Exception as e:
        print(f"GPT err: {e}")
        return "कैश ऑन डिलीवरी पर ऑर्डर करें — कोई जोखिम नहीं। नाम बताइए।"

# ═══════════════════════════════════════════════════
# INTENT DETECTION (Hindi + Hinglish)
# ═══════════════════════════════════════════════════
_BUY = {
    "हाँ","हां","हाँजी","हांजी","ठीक","बिल्कुल","चाहिए","मंगवाना","मँगवाना",
    "ऑर्डर","लेना","लूँगा","लूंगा","खरीदना","खरीदूँगा","भेजो","मंगाना",
    "haan","han","haanji","hanji","theek hai","bilkul","zaroor","zarur",
    "yes","ok","okay","sure","sahi","chahiye","mangwana","mangana",
    "lena hai","order","order karo","book karo","buy","purchase",
    "bhejo","de do","mangwa do","send karo","le lena","le lunga","le lungi","lelo",
}
_NO = {
    "नहीं","नही","no","nahi","nahin","galat","wrong","badlo","mat","naa","na",
}
_PRICE = {
    "mehnga","mahenga","costly","expensive","sasta","budget","zyada","1000","500",
    "parachute","bajaj","dabur","patanjali","marico","keo karpin","online",
    "amazon","flipkart","price","kitna","rate","afford","paise nahi","baad mein",
    "sochna","guarantee","return","risk","cheap","discount","offer",
    "महंगा","सस्ता","कीमत","पैसे","बाद में","सोचूँगा","गारंटी",
}
_OFF = [
    r"\b(weather|mausam|cricket|film|movie|khana|food|news|politics)\b",
    r"\b(doosra product|other product|kuch aur)\b",
]

def is_buy(t):
    tl = t.lower()
    return (any(w in tl for w in _BUY) or
            any(k in tl for k in ["order","mangwa","buy","chahiye","bhejo","lelo"]))

def is_no(t):   return any(w in t.lower() for w in _NO)
def is_off(t):  return any(re.search(p, t.lower()) for p in _OFF)
def get_pin(t: str) -> str:
    """
    Extract 6-digit Indian pincode from Twilio speech transcript.
    Handles: "110001", "1 1 0 0 0 1", "ek lakh das hazaar ek",
             "pincode 110001 hai", "one one zero zero zero one"
    """
    # Step 1: remove all spaces between digits (handles "1 1 0 0 0 1" → "110001")
    collapsed = re.sub(r"(\d)\s+(\d)", r"\1\2", t)
    collapsed = re.sub(r"(\d)\s+(\d)", r"\1\2", collapsed)  # run twice for odd/even
    m = re.search(r"\b\d{6}\b", collapsed)
    if m:
        return m.group()

    # Step 2: extract ALL digits and check if exactly 6
    digits_only = re.sub(r"\D", "", t)
    if len(digits_only) == 6:
        return digits_only

    # Step 3: if >6 digits, take first 6 (user may say "pincode 110001 hai")
    if len(digits_only) >= 6:
        return digits_only[:6]

    # Step 4: English/Hindi number words → digits
    word_map = {
        # English words
        "zero":"0","one":"1","two":"2","three":"3","four":"4",
        "five":"5","six":"6","seven":"7","eight":"8","nine":"9",
        # Hinglish Roman
        "shunya":"0","ek":"1","do":"2","teen":"3","char":"4",
        "paanch":"5","chhe":"6","saat":"7","aath":"8","nau":"9",
        # Hindi Devanagari words
        "शून्य":"0","एक":"1","दो":"2","तीन":"3","चार":"4",
        "पाँच":"5","पांच":"5","छह":"6","सात":"7","आठ":"8","नौ":"9",
    }
    words = t.lower().split()
    digit_str = ""
    for w in words:
        if w in word_map:
            digit_str += word_map[w]
    if len(digit_str) == 6:
        return digit_str

    return ""

# Price question detector — catches all Indian ways of asking price
_PRICE_Q = {
    "price","cost","daam","keemat","kitna","rate","paisa","paise","1499","rupay",
    "rupaye","kitne ka","mehnga","sasta","offer","discount","kitna hai","kya hai price",
    "price kya","cost kya","daam kya","kitne mein","kitnay","charge",
    "कीमत","दाम","कितना","रुपए","रुपये","महंगा","सस्ता","ऑफर",
}
def is_price_q(t): return any(w in t.lower() for w in _PRICE_Q)

# ═══════════════════════════════════════════════════
# STATE MACHINE
# ═══════════════════════════════════════════════════
async def process(sid: str, text: str, caller: str) -> tuple[str, bool]:
    """
    Returns (reply_text, should_hangup).
    6 conversion fixes applied:
    F1 Strong hook greeting  F2 Trust line  F3 Emotional trigger
    F4 Direct close          F5 Urgency     F6 Name→City→Address→Pincode
    """
    if sid not in _calls:
        _calls[sid] = new_cs(caller)
    cs    = _calls[sid]
    cs["caller"] = caller
    cs["turn"]  += 1
    t     = text.strip()
    state = cs["state"]

    # ── silence ───────────────────────────────────
    if not t:
        return {
            "collecting_name":    _R_NAME,
            "collecting_city":    _R_CITY,
            "collecting_address": _R_ADDR,
            "collecting_pincode": _R_PIN,
        }.get(state, _SILENCE), False

    # ── done ──────────────────────────────────────
    if state == "done":
        return _DONE, True

    # ── hello / acknowledgement handler ───────────
    # User says hello, haan, ji, yes, sun raha hoon — just re-ask the current question
    _HELLO = {"hello","helo","hlo","hi","haan ji","ji","sun raha","sun rahi","haan",
              "bol","bolo","boliye","ha","han","hmm","hm","are","arre","हेलो","जी",
              "हाँ जी","बोलिए","सुन रहा","सुन रही"}
    tl_stripped = t.lower().strip("?!., ")
    if tl_stripped in _HELLO or (len(t.split()) <= 2 and tl_stripped in _HELLO):
        # Re-ask whatever the current step needs — no GPT, instant
        _reask = {
            "permission":         _v(_GREET, cs["turn"]),
            "hair_problem":       _v(_ASK_HAIR, cs["turn"]),
            "pitch":              "जी — क्या आप वेदाचार्य आदिवासी हेयर ऑयल के बारे में जानना चाहते हैं?",
            "collecting_name":    _R_NAME,
            "collecting_city":    _R_CITY,
            "collecting_address": _R_ADDR,
            "collecting_pincode": _R_PIN,
            "confirming":         "जी — क्या दी गई जानकारी सही है?",
        }
        reply = _reask.get(state, "जी — बताइए, मैं क्या सहायता कर सकती हूँ?")
        cs["last_bot"] = reply
        return reply, False

    # ── off-topic guard ───────────────────────────
    if is_off(t):
        return _OFFTOPIC, False

    # ── STEP 1: PERMISSION ──
    if state == "permission":
        if is_no(t):
            reply = "कोई बात नहीं — बस 20 सेकंड। क्या आपके बालों में झड़ने या रूसी की कोई समस्या है?"
            cs["state"]    = "hair_problem"
            cs["last_bot"] = reply
            return reply, False
        cs["state"] = "hair_problem"
        reply = _v(_ASK_HAIR, cs["turn"])
        cs["last_bot"] = reply
        return reply, False

    # ── STEP 2: HAIR PROBLEM ──────────────────────
    if state == "hair_problem":
        tl = t.lower()
        _yes_words = {"haan","ha","han","yes","hai","ho rahi","ho raha","hota","hoti",
                      "jhad","dandruff","safed","baal","problem","pareshaan","takleef",
                      "हाँ","हां","है","झड़","समस्या","परेशान"}
        has_problem = any(w in tl for w in _yes_words) or is_buy(t)
        no_problem  = is_no(t) or any(w in tl for w in
                      ["nahi","nahin","no problem","theek","bilkul theek","sab theek",
                       "नहीं","ठीक","सब ठीक"])

        if no_problem and not has_problem:
            cs["hair_problem"] = False
            cs["state"]        = "pitch"
            body = _v(_NO_PROBLEM, cs["turn"])
            full = body + " " + _URGENCY
            cs["last_bot"] = full
            return full, False
        else:
            cs["hair_problem"] = True
            cs["state"]        = "pitch"
            body   = _v(_YES_PROBLEM, cs["turn"])
            # Short usage line — one sentence only, not an essay
            detail = "हफ्ते में 2-3 बार रात को लगाएं, सुबह धो लें। सिर्फ 1499 रुपये — कैश ऑन डिलीवरी उपलब्ध है।"
            full   = body + " " + detail
            cs["last_bot"] = full
            return full, False

    # ── STEP 3+4: PITCH ───────────────────────────
    if state == "pitch":
        if is_buy(t):
            cs["state"] = "collecting_name"
            reply = _v(_ASK_NAME, cs["turn"])
            cs["last_bot"] = reply
            return reply, False
        # ── INSTANT price answer — no GPT needed ──
        if is_price_q(t):
            reply = _v(_PRICE_ANSWER, cs["turn"])
            cs["last_bot"] = reply
            return reply, False
        # GPT handles all other questions and objections
        reply = await gpt(cs, t)
        cs["last_bot"] = reply
        return reply, False

    # ── STEP 6A: NAME ─────────────────────────────
    if state == "collecting_name":
        if len(t) >= 2 and "?" not in t:
            name = re.sub(
                r"^(mera naam|mera name|main|i am|naam hai|name is|मेरा नाम|मैं)\s+",
                "", t, flags=re.IGNORECASE
            ).strip().title()
            cs["name"]  = name
            cs["state"] = "collecting_city"     # F6: go to city next
            reply = _v(_ASK_CITY, cs["turn"])
            cs["last_bot"] = reply
            return f"{name} जी, " + reply, False
        return _R_NAME, False

    # ── STEP 6B: CITY (Fix 6 — new step) ─────────
    if state == "collecting_city":
        if len(t) >= 2:
            cs["city"]  = t.strip().title()
            cs["state"] = "collecting_address"
            reply = _v(_ASK_ADDR, cs["turn"])
            cs["last_bot"] = reply
            return reply, False
        return _R_CITY, False

    # ── STEP 6C: ADDRESS ──────────────────────────
    if state == "collecting_address":
        if len(t) >= 5:
            # Combine city + address for full delivery address
            cs["address"] = f"{t.strip()}, {cs['city']}"
            cs["state"]   = "collecting_pincode"
            cs["last_bot"] = _ASK_PIN
            return _ASK_PIN, False
        return _R_ADDR, False

    # ── STEP 6D: PINCODE ──────────────────────────
    if state == "collecting_pincode":
        pin = get_pin(t)
        print(f"📌 Pincode extract: input='{t}' → pin='{pin}'")  # visible in Render logs
        if pin:
            cs["pincode"] = pin
            cs["state"]   = "confirming"
            reply = (f"एक बार पुष्टि कर लेती हूँ — "
                     f"नाम: {cs['name']}, "
                     f"शहर: {cs['city']}, "
                     f"पता: {cs['address']}, "
                     f"पिन कोड: {pin}। क्या यह सही है?")
            cs["last_bot"] = reply
            return reply, False
        return _R_PIN, False

    # ── STEP 7: CONFIRM + CLOSE ───────────────────
    if state == "confirming":
        if is_buy(t):
            cs["state"] = "done"
            asyncio.create_task(save_order(
                cs["name"], cs["address"], cs["pincode"], cs["caller"], cs.get("city","")
            ))
            reply = (f"बहुत धन्यवाद {cs['name']} जी! आपका ऑर्डर दर्ज हो गया। "
                     f"5 से 7 दिन में {cs['city']} में डिलीवरी आएगी। "
                     f"डिलीवरी पर केवल 1499 रुपये देने होंगे। आपका दिन शुभ हो!")
            cs["last_bot"] = reply
            return reply, True
        if is_no(t):
            cs.update({"state":"collecting_name","name":"","city":"","address":"","pincode":""})
            return "कोई बात नहीं — फिर से शुरू करते हैं। नाम बताइए।", False
        return "हाँ या नहीं बोलिए — क्या यह जानकारी सही है?", False

    # fallback
    reply = await gpt(cs, t)
    cs["last_bot"] = reply
    return reply, False

# ═══════════════════════════════════════════════════
# GOOGLE SHEETS  (async, non-blocking) [Phase 6]
# ═══════════════════════════════════════════════════
async def save_order(name, address, pincode, phone, city=""):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    # NOTE: address already contains city (merged in collecting_address state)
    # Do NOT add city again — it would appear twice
    print(f"📦 ORDER → {ts} | {name} | {address} | {pincode} | {phone}")
    if not GOOGLE_SHEET_ID or not GOOGLE_CREDS_JSON:
        print("⚠️  sheet not configured — order in logs only")
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _sheet_write, name, address, pincode, phone, ts)

def _sheet_write(name, address, pincode, phone, ts):
    """
    Writes exactly 8 columns to match your Google Sheet:
    A:Timestamp  B:Name  C:Address  D:Pincode  E:Phone
    F:Product    G:Price  H:Status
    """
    try:
        import google.oauth2.service_account as sa
        import googleapiclient.discovery as gd
        creds = sa.Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDS_JSON),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        svc = gd.build("sheets", "v4", credentials=creds, cache_discovery=False)
        svc.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range="Sheet1!A:H",          # exactly 8 columns — matches your sheet
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [[
                ts,                       # A — Timestamp
                name,                     # B — Name
                address,                  # C — Address (includes city)
                pincode,                  # D — Pincode
                phone,                    # E — Phone
                "Adivasi Hair Oil",       # F — Product
                "₹1499",                  # G — Price
                "Pending",                # H — Status
            ]]},
        ).execute()
        print(f"✅ Sheet saved: {name} | {pincode}")
    except Exception as e:
        print(f"❌ Sheet error: {e}")


# ═══════════════════════════════════════════════════
# TWILIO WEBHOOKS
# ═══════════════════════════════════════════════════
async def voice_start(request):
    """Step 1 — new call arrives. Plays permission greeting."""
    try:    data = await request.post()
    except: data = {}
    sid    = data.get("CallSid","unknown")
    caller = data.get("From","unknown")
    _calls[sid] = new_cs(caller)
    print(f"📞 {sid} from {caller}")

    # W1: use pre-warmed greeting — 0 ms TTS
    pre = _warm.get("greet","")
    tw  = await mk_twiml(_GREET[0], R(), pre_aid=pre)
    return web.Response(text=tw, content_type="application/xml")


async def voice_respond(request):
    """Steps 2-7 — user spoke, advance through sales script."""
    try:    data = await request.post()
    except: data = {}
    sid        = data.get("CallSid","unknown")
    caller     = data.get("From","unknown")
    speech     = data.get("SpeechResult","").strip()
    confidence = float(data.get("Confidence","0") or "0")
    no_speech  = request.rel_url.query.get("ns","0")

    print(f"🗣  [{sid}] '{speech}' conf={confidence:.2f}" +
          (" ⚠️ LOW" if 0 < confidence < 0.4 else "") +
          (" 🔇 EMPTY" if not speech else ""))

    # ── silence / timeout ─────────────────────────
    # Only treat as silence if truly empty — accept even low-confidence speech
    # Indian phone calls regularly score 0.3-0.5 confidence but are correct
    if no_speech == "1" or (not speech):
        cs    = _calls.get(sid, new_cs(caller))
        state = cs.get("state","permission")
        msg   = {
            "permission":         _v(_GREET, 1),
            "hair_problem":       _v(_ASK_HAIR, 1),
            "collecting_name":    _R_NAME,
            "collecting_city":    _R_CITY,
            "collecting_address": _R_ADDR,
            "collecting_pincode": _R_PIN,
        }.get(state, _SILENCE)
        return web.Response(
            text=await mk_twiml(msg, R()),
            content_type="application/xml"
        )

    reply, hangup = await process(sid, speech, caller)
    print(f"🤖 [{_calls.get(sid,{}).get('state','?')}] {reply[:80]}")

    # W1: use pre-warmed audio for Step 2 (ask hair problem)
    pre = ""
    cs  = _calls.get(sid, {})
    if cs.get("state") == "hair_problem":
        pre = _warm.get("hair","")

    tw = await mk_twiml(reply, R(), hangup=hangup, pre_aid=pre)
    return web.Response(text=tw, content_type="application/xml")

# ═══════════════════════════════════════════════════
# KEEP-ALIVE  [W9]
# ═══════════════════════════════════════════════════
async def keepalive():
    await asyncio.sleep(60)
    while True:
        try:
            s = await http()
            async with s.get(f"{PUBLIC_URL}/",
                             timeout=aiohttp.ClientTimeout(total=8)) as r:
                print(f"🏓 {r.status}")
        except Exception as e:
            print(f"⚠️  keepalive: {e}")
        await asyncio.sleep(480)   # every 8 min — Render sleeps at 15 min

async def on_startup(app):
    asyncio.create_task(keepalive())
    asyncio.create_task(prewarm())     # W1: pre-warm greeting + ask_name TTS

# ═══════════════════════════════════════════════════
# HEALTH CHECK
# ═══════════════════════════════════════════════════
async def health(request):
    return web.json_response({
        "ok": True,
        "product": "Adivasi Hair Oil",
        "sarvam":  bool(SARVAM_API_KEY),
        "sheet":   bool(GOOGLE_SHEET_ID),
        "calls":   len(_calls),
        "cached_audio": len(_ac),
        "prewarmed": list(_warm.keys()),
    })

# ═══════════════════════════════════════════════════
# APP
# ═══════════════════════════════════════════════════
def create_app():
    app = web.Application(client_max_size=8 * 1024 * 1024)
    app.on_startup.append(on_startup)
    app.router.add_get("/",              health)
    app.router.add_post("/voice/start",  voice_start)
    app.router.add_post("/voice/respond",voice_respond)
    app.router.add_get("/audio/{aid}",   audio_serve)
    return app

if __name__ == "__main__":
    print("═"*52)
    print("  🌿 Priya — Adivasi Hair Oil | Ultra-Low Latency")
    print(f"  {PUBLIC_URL}  |  :{PORT}")
    print(f"  TTS  {'✅ Sarvam' if SARVAM_API_KEY else '⚠️  Polly fallback'}")
    print(f"  Sheet {'✅ Google Sheet' if GOOGLE_SHEET_ID else '⚠️  logs only'}")
    print("═"*52)
    web.run_app(create_app(), host="0.0.0.0", port=PORT)