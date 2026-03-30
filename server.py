"""
server.py — Priya | Vedacharya Adivasi Hair Oil | ULTRA-LOW LATENCY BUILD v4
=============================================================================

3 ISSUES FIXED IN v4:
  ISSUE1 FIX — Response delay from transcript logging
    Root cause: log_transcript used run_in_executor which competes with
    the call thread pool. Both user log + Priya log fired as separate tasks,
    each making an individual Google API call = 2 slow API calls per turn.
    Fix: Single combined sheet write per turn (user + priya in one call).
    Also added asyncio.sleep(0) to yield event loop before logging starts.

  ISSUE2 FIX — Bot gives wrong reply to short Hindi inputs
    Root cause: GPT only received the current 1-2 word input with no context
    of what Priya last said. "Haan", "kitna", "kya" look ambiguous without
    knowing the previous bot message.
    Fix: System prompt now has 20+ short Hindi Q&A examples. GPT also
    receives last 3 conversation turns as history (not just last_bot snippet).
    Confidence < 0.5 now triggers a clarification reprompt instead of
    passing garbled text to GPT.

  ISSUE3 FIX — Transcript rows out of order in Sheet2
    Root cause: User speech log and Priya reply log were fired as two
    separate asyncio tasks. Whichever Google API call finished first
    got written first — race condition.
    Fix: Both rows now written together in ONE atomic batch append call
    with a turn_seq number column added so Sheet2 can always be sorted
    correctly even if rows arrive slightly out of order.

ORIGINAL LATENCY WINS (kept):
  W1. Pre-warmed TTS at startup                → 0 ms TTS on cached turns
  W2. GPT only in pitch state                  → 0 ms GPT for 90% of turns
  W3. Persistent aiohttp sessions              → saves ~100 ms per API call
  W4. max_tokens=40, temperature=0.10         → GPT replies faster
  W5. Static replies for structured states    → 0 ms GPT
  W6. speechTimeout="auto"                    → Twilio cuts silence fast
  W7. RAM audio cache                         → no disk I/O
  W8. Keep-alive every 8 min                  → Render never cold-starts
  W9. text[:250] to Sarvam                    → shorter audio plays faster
  W10. Google Sheets client built once        → no rebuild per call
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
# PERSISTENT HTTP SESSION
# ═══════════════════════════════════════════════════
_http: aiohttp.ClientSession | None = None

async def http() -> aiohttp.ClientSession:
    global _http
    if _http is None or _http.closed:
        connector = aiohttp.TCPConnector(limit=30, ttl_dns_cache=300, force_close=False)
        _http = aiohttp.ClientSession(connector=connector)
    return _http

# ═══════════════════════════════════════════════════
# GOOGLE SHEETS — built ONCE at startup
# ═══════════════════════════════════════════════════
_sheets_svc = None

def _build_sheets_service():
    global _sheets_svc
    if _sheets_svc is not None:
        return _sheets_svc
    if not GOOGLE_SHEET_ID or not GOOGLE_CREDS_JSON:
        return None
    try:
        import google.oauth2.service_account as sa
        import googleapiclient.discovery as gd
        creds = sa.Credentials.from_service_account_info(
            json.loads(GOOGLE_CREDS_JSON),
            scopes=["https://www.googleapis.com/auth/spreadsheets"],
        )
        _sheets_svc = gd.build("sheets", "v4", credentials=creds, cache_discovery=False)
        print("✅ Google Sheets service built at startup")
        return _sheets_svc
    except Exception as e:
        print(f"❌ Sheets build error: {e}")
        return None

# ═══════════════════════════════════════════════════
# PRODUCT KNOWLEDGE
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

अगर ग्राहक को कोई समस्या नहीं है:
- आजकल प्रदूषण और तनाव की वजह से बाल झड़ना कभी भी शुरू हो सकता है
- रोकथाम के लिए यह 100% प्राकृतिक तेल एक बेहतरीन विकल्प है
- भविष्य की समस्याओं से बचने के लिए अभी से शुरू करना सही रहेगा

अगर ग्राहक को समस्या है:
- समझ सकती हूँ, आजकल यह समस्या बहुत आम हो गई है
- यह तेल विशेष रूप से बाल झड़ने, रूसी और बाल उगाने के लिए बना है
- प्राकृतिक जड़ी-बूटियाँ सिर की त्वचा को पोषण देती हैं और धीरे-धीरे बाल घने करती हैं

कीमत पर आपत्ति:
- महंगा: 500ml 2-3 महीने चलता है — सिर्फ 16 रुपये प्रतिदिन। सैलून 500-2000 रुपये एक बार में।
- सस्ता चाहिए: सस्ते में खनिज तेल होता है — असली जड़ी-बूटियाँ नहीं। 418 ग्राहक प्रमाण हैं।
- पतंजलि/डाबर/पैराशूट: रोज़मर्रा के तेल हैं। यह 108 जड़ी-बूटियों का औषधीय फ़ॉर्मूला है।
- पैसे नहीं: कैश ऑन डिलीवरी है — घर पर मिलने पर दें। 7 दिन वापसी नीति। कोई जोखिम नहीं।
- गारंटी: 7 दिन वापसी। 418 में से किसी ने वापस नहीं किया — असर मिलता है।
"""

# ═══════════════════════════════════════════════════
# ISSUE2 FIX — SYSTEM PROMPT
# Added 20+ short Hindi Q&A examples so GPT understands
# single-word and 2-word inputs correctly in context.
# GPT also receives last 3 turns as conversation history.
# ═══════════════════════════════════════════════════
SYSTEM_PROMPT = f"""Tum Priya ho — Teleone ki vinammra sales executive ho.
Tumhara kaam: Vedacharya Adivasi Hair Oil bechna aur order lena.

SABSE ZAROORI: Jawab HAMESHA SHUDDH HINDI MEIN. Koi Hinglish, koi English word nahi.

CHHOTE SAWAAL — inhe context ke saath samjho (last_reply dekho):
Agar last reply mein price poochi thi aur user ne kaha:
- "kitna" / "kya" / "bolo" → कीमत फिर बताओ: "1499 रुपये — कैश ऑन डिलीवरी।"
- "theek" / "sahi" / "haan" → order ki taraf: "बहुत अच्छा! नाम बताइए।"
- "nahi" / "mahanga" → price objection handle karo
- "kaise" / "kya hai" → product explain karo briefly
- "acha" / "ok" → order push karo: "तो नाम बताइए — ऑर्डर करते हैं।"
- "soch" / "baad" → urgency: "आज ही पुष्टि करें — छूट सीमित है।"
- "guarantee" / "pakka" → "7 दिन वापसी नीति है — कोई जोखिम नहीं।"
- "kitne din" → "30 दिन में असर दिखता है।"
- "side effect" / "nuksan" → "100% प्राकृतिक — कोई साइड इफेक्ट नहीं।"
- "kaha se" / "original" → "वेदाचार्य का सीधा — 418 संतुष्ट ग्राहक।"
- "delivery" / "kab aayega" → "5-7 दिन में डिलीवरी।"
- "cod" / "online" → "कैश ऑन डिलीवरी है — घर पर मिलने पर दें।"
- "return" / "wapas" → "7 दिन में वापस कर सकते हैं।"
- "free" / "sample" → "अभी 1499 में पूरी बोतल — COD पर।"
- "bhai" / "sun" / "ek minute" → product ke baare mein continue karo
- "number" / "whatsapp" → "ऑर्डर अभी फ़ोन पर ही ले लेती हूँ — नाम बताइए।"

{_KB}

Niyam:
1. SIRF SHUDDH HINDI — Roman nahi, Hinglish nahi.
2. SIRF 1-2 chhote vaakya. Zyaada KABHI nahi.
3. Mat bolo: hmmm, achha, oh, ji haan bilkul, dekhiye, toh.
4. Pichli baat mat dohraao — conversation history dekho.
5. Har jawab order ki taraf le jao.
6. User haan/order/chahiye kahe → turant naam poochho.
7. Agar user ka sawaal 1-2 words ka hai → context (last_reply) se samjho.""".strip()

# ═══════════════════════════════════════════════════
# CALL STATE
# Added: history list to track last 3 turns for GPT context
# Added: turn_seq counter for Sheet2 ordering fix
# ═══════════════════════════════════════════════════
_calls: dict[str, dict] = {}

def new_cs(caller=""):
    return {
        "state":        "permission",
        "hair_problem": None,
        "name":         "", "city": "", "address": "", "pincode": "",
        "caller":       caller,
        "turn":         0,
        "turn_seq":     0,          # ISSUE3 FIX: global sequence for Sheet2 ordering
        "last_bot":     "",
        "history":      [],         # ISSUE2 FIX: last 3 turns [{"user":..,"bot":..}, ...]
    }

# ═══════════════════════════════════════════════════
# STATIC REPLIES
# ═══════════════════════════════════════════════════
_GREET = [
    "नमस्ते! मैं प्रिया बोल रही हूँ वेदाचार्य से। आजकल बहुत लोगों के बाल झड़ रहे हैं — क्या एक मिनट मिलेगा?",
    "नमस्ते! वेदाचार्य से प्रिया बोल रही हूँ। बालों के बारे में ज़रूरी बात थी — क्या अभी बात हो सकती है?",
    "नमस्ते! प्रिया हूँ वेदाचार्य से। बालों की अहम बात थी — एक मिनट उपलब्ध है?",
]
_ASK_HAIR = [
    "क्या बाल झड़ने, रूसी, सफेद बाल या कमज़ोरी की कोई समस्या है?",
    "बालों में कोई परेशानी है — झड़ना, रूसी या नए बाल नहीं उग रहे?",
    "बालों से जुड़ी कोई तकलीफ है आपको?",
]
_NO_PROBLEM = [
    "अच्छा है। पर आजकल प्रदूषण से बाल झड़ना कभी भी शुरू हो सकता है। 418 लोग पहले से यह तेल लगाकर बाल मज़बूत रख रहे हैं।",
    "ठीक है। पर जिन्हें समस्या नहीं थी उन्हें भी अचानक बाल झड़ने लगे — पानी, धूल, तनाव का असर होता है।",
]
_YES_PROBLEM = [
    "समझ सकती हूँ — समय पर ध्यान न दें तो बाल और कम होते हैं। इस तेल में 108 जड़ी-बूटियाँ हैं जो बालों को अंदर से मज़बूत करती हैं।",
    "बिल्कुल — जितनी जल्दी शुरू करें उतना बेहतर। भृंगराज और आंवला बाल झड़ना रोकते हैं। 418 लोग अच्छा असर पा रहे हैं।",
]
_URGENCY      = "यह छूट सीमित समय के लिए है — आज ही पुष्टि कर लें।"
_PRICE_ANSWER = [
    "1499 रुपये — MRP 2799 था, 46% छूट। कैश ऑन डिलीवरी है, घर पर आने पर पैसे देने होंगे।",
    "सिर्फ 1499 में 500 मिली की बोतल। पहले उत्पाद देखें, फिर पैसे दें।",
    "1499 रुपये — और 7 दिन की वापसी नीति भी है। कोई जोखिम नहीं।",
]
_ASK_NAME   = ["पहले अपना पूरा नाम बताइए।", "आपका नाम क्या है?", "नाम बताइए।"]
_ASK_CITY   = ["आप कौन से शहर में हैं?", "शहर का नाम बताइए।", "डिलीवरी कहाँ करनी है?"]
_ASK_ADDR   = ["घर का पता बताइए — गली और मोहल्ला।", "गली नंबर या कॉलोनी बताइए।", "पूरा पता बताइए।"]
_ASK_PIN    = "पिन कोड क्या है?"
_R_NAME     = "नाम स्पष्ट रूप से एक बार और बताइए।"
_R_CITY     = "शहर का नाम फिर से बताइए।"
_R_ADDR     = "पता थोड़ा विस्तार से बताइए।"
_R_PIN      = "पिन कोड बताइए — छह अंक, एक-एक करके बोलें।"
_SILENCE    = "सुनाई नहीं दिया — कृपया दोबारा बोलें।"
_LOW_CONF   = "ठीक से सुनाई नहीं दिया — एक बार और बताइए?"  # ISSUE2 FIX
_OFFTOPIC   = "मैं केवल आदिवासी हेयर ऑयल के बारे में जानकारी दे सकती हूँ।"
_DONE       = "धन्यवाद! आपका ऑर्डर हो गया।"

def _v(lst, n): return lst[n % len(lst)]

# ═══════════════════════════════════════════════════
# PRE-WARM ALL STATIC REPLIES AT STARTUP
# ═══════════════════════════════════════════════════
_ac:   dict[str, bytes] = {}
_warm: dict[str, str]   = {}

_PREWARM_MAP = {
    "greet":    _GREET[0],
    "hair":     _ASK_HAIR[0],
    "ask_name": _ASK_NAME[0],
    "ask_city": _ASK_CITY[0],
    "ask_addr": _ASK_ADDR[0],
    "ask_pin":  _ASK_PIN,
    "r_name":   _R_NAME,
    "r_pin":    _R_PIN,
    "silence":  _SILENCE,
    "low_conf": _LOW_CONF,          # ISSUE2 FIX: pre-warm clarification prompt
}

async def prewarm():
    tasks = {key: tts(text) for key, text in _PREWARM_MAP.items()}
    results = await asyncio.gather(*tasks.values(), return_exceptions=True)
    for key, audio in zip(tasks.keys(), results):
        if isinstance(audio, bytes) and audio:
            _ac[f"w_{key}"] = audio
            _warm[key] = f"w_{key}"
            print(f"🔥 pre-warmed [{key}]")
        else:
            print(f"⚠️  pre-warm failed [{key}]")

# ═══════════════════════════════════════════════════
# SARVAM TTS
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
                "pitch":                0.0,
                "pace":                 1.0,
                "loudness":             1.0,
                "speech_sample_rate":   16000,
                "enable_preprocessing": True,
                "model":                "bulbul:v2",
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
    return web.Response(
        body=audio,
        content_type="audio/wav",
        headers={"Content-Disposition": "inline", "Cache-Control": "no-cache",
                 "Accept-Ranges": "bytes"}
    )

# ═══════════════════════════════════════════════════
# TWIML BUILDER
# ═══════════════════════════════════════════════════
def _xe(t): return t.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;")

_GATHER = (
    '<Gather input="speech" action="{a}" method="POST" '
    'language="hi-IN" enhanced="true" speechModel="phone_call" '
    'speechTimeout="auto" '
    'timeout="5" '
    'profanityFilter="false" '
    'hints="हाँ,नहीं,हां,जी,ठीक है,बिल्कुल,चाहिए,ऑर्डर,नाम,पता,पिनकोड,हेलो,जी हाँ,हाँ जी,'
    'नमस्ते,बाल,तेल,झड़ना,रूसी,मंगवाना,ज़रूर,कितना,कैसे,क्या,कब,कहाँ,गारंटी,वापसी,'
    'दिल्ली,मुंबई,कोलकाता,चेन्नई,बेंगलुरु,हैदराबाद,पुणे,जयपुर,लखनऊ,सूरत,'
    'haan,nahi,theek,bilkul,order,pincode,address,price,kitna,kya,kaise,soch,'
    'guarantee,delivery,cod,return,side,effect,original,sample,'
    'ek,do,teen,char,paanch,chhe,saat,aath,nau,shunya,'
    'zero,one,two,three,four,five,six,seven,eight,nine">'
)

async def mk_twiml(text: str, action: str, hangup=False, pre_aid: str = "") -> str:
    if pre_aid and pre_aid in _ac:
        aid = pre_aid
    else:
        audio = await tts(text)
        if audio:
            aid = f"a{id(audio)%99999999:08d}"
            _ac[aid] = audio
        else:
            aid = ""

    inner = (f'<Play>{PUBLIC_URL}/audio/{aid}</Play>' if aid
             else f'<Say language="hi-IN" voice="Polly.Kajal">{_xe(text)}</Say>')

    if hangup:
        return (f'<?xml version="1.0" encoding="UTF-8"?>'
                f'<Response>{inner}<Hangup/></Response>')

    go  = _GATHER.format(a=action)
    red = f'<Redirect method="POST">{action}?ns=1</Redirect>'
    return (f'<?xml version="1.0" encoding="UTF-8"?>'
            f'<Response>{go}{inner}</Gather>{red}</Response>')

# ═══════════════════════════════════════════════════
# ISSUE2 FIX — GPT with conversation history
# Now receives last 3 turns as proper chat history
# so short inputs like "haan" or "kitna" are understood
# in full context of what Priya previously said.
# ═══════════════════════════════════════════════════
async def gpt(cs: dict, user_text: str) -> str:
    ctx = (f"[state={cs['state']}"
           + (f"|name={cs['name']}" if cs["name"] else "")
           + (f"|last_reply={cs['last_bot'][:60]}" if cs["last_bot"] else "")
           + "]")

    # Build messages with conversation history for context
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": ctx},
    ]

    # Add last 3 turns of history so GPT understands short inputs
    for turn in cs.get("history", [])[-3:]:
        if turn.get("user"):
            messages.append({"role": "user",      "content": turn["user"]})
        if turn.get("bot"):
            messages.append({"role": "assistant", "content": turn["bot"]})

    # Add current user input
    messages.append({"role": "user", "content": user_text})

    try:
        s = await http()
        async with s.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}",
                     "Content-Type": "application/json"},
            json={
                "model":       "gpt-4o-mini",
                "messages":    messages,
                "max_tokens":  40,
                "temperature": 0.1,
            },
            timeout=aiohttp.ClientTimeout(total=5),
        ) as r:
            d = await r.json()
            reply = d["choices"][0]["message"]["content"].strip()
            reply = re.sub(r"^(प्रिया:|Priya:|Bot:)\s*", "", reply).strip()
            return reply
    except Exception as e:
        print(f"GPT err: {e}")
        return "कैश ऑन डिलीवरी पर ऑर्डर करें — कोई जोखिम नहीं। नाम बताइए।"

# ═══════════════════════════════════════════════════
# INTENT DETECTION
# ═══════════════════════════════════════════════════
_BUY = {
    "हाँ","हां","हाँजी","हांजी","ठीक","बिल्कुल","चाहिए","मंगवाना","मँगवाना",
    "ऑर्डर","लेना","लूँगा","लूंगा","खरीदना","खरीदूँगा","भेजो","मंगाना",
    "haan","han","haanji","hanji","theek hai","bilkul","zaroor","zarur",
    "yes","ok","okay","sure","sahi","chahiye","mangwana","mangana",
    "lena hai","order","order karo","book karo","buy","purchase",
    "bhejo","de do","mangwa do","send karo","le lena","le lunga","le lungi","lelo",
}
_NO = {"नहीं","नही","no","nahi","nahin","galat","wrong","badlo","mat","naa","na"}
_OFF = [
    r"\b(weather|mausam|cricket|film|movie|khana|food|news|politics)\b",
    r"\b(doosra product|other product|kuch aur)\b",
]
_PRICE_Q = {
    "price","cost","daam","keemat","kitna","rate","paisa","paise","1499","rupay",
    "rupaye","kitne ka","mehnga","sasta","offer","discount","kitna hai","kya hai price",
    "price kya","cost kya","daam kya","kitne mein","kitnay","charge",
    "कीमत","दाम","कितना","रुपए","रुपये","महंगा","सस्ता","ऑफर",
}

def is_buy(t):
    tl = t.lower()
    return any(w in tl for w in _BUY) or any(k in tl for k in ["order","mangwa","buy","chahiye","bhejo","lelo"])
def is_no(t):       return any(w in t.lower() for w in _NO)
def is_off(t):      return any(re.search(p, t.lower()) for p in _OFF)
def is_price_q(t):  return any(w in t.lower() for w in _PRICE_Q)

def get_pin(t: str) -> str:
    collapsed = re.sub(r"(\d)\s+(\d)", r"\1\2", t)
    collapsed = re.sub(r"(\d)\s+(\d)", r"\1\2", collapsed)
    m = re.search(r"\b\d{6}\b", collapsed)
    if m: return m.group()
    digits_only = re.sub(r"\D", "", t)
    if len(digits_only) >= 6: return digits_only[:6]
    word_map = {
        "zero":"0","one":"1","two":"2","three":"3","four":"4",
        "five":"5","six":"6","seven":"7","eight":"8","nine":"9",
        "shunya":"0","ek":"1","do":"2","teen":"3","char":"4",
        "paanch":"5","chhe":"6","saat":"7","aath":"8","nau":"9",
        "शून्य":"0","एक":"1","दो":"2","तीन":"3","चार":"4",
        "पाँच":"5","पांच":"5","छह":"6","सात":"7","आठ":"8","नौ":"9",
    }
    digit_str = "".join(word_map[w] for w in t.lower().split() if w in word_map)
    return digit_str if len(digit_str) == 6 else ""

# ═══════════════════════════════════════════════════
# STATE MACHINE
# ═══════════════════════════════════════════════════
async def process(sid: str, text: str, caller: str) -> tuple[str, bool, bool, str]:
    if sid not in _calls:
        _calls[sid] = new_cs(caller)
    cs    = _calls[sid]
    cs["caller"] = caller
    cs["turn"]  += 1
    t     = text.strip()
    state = cs["state"]

    def static(reply, next_state=None, pre=""):
        if next_state: cs["state"] = next_state
        cs["last_bot"] = reply
        return reply, False, False, pre

    if not t:
        msg = {
            "collecting_name":    _R_NAME,
            "collecting_city":    _R_CITY,
            "collecting_address": _R_ADDR,
            "collecting_pincode": _R_PIN,
        }.get(state, _SILENCE)
        return static(msg, pre=_warm.get("r_name","") if state=="collecting_name"
                      else _warm.get("r_pin","") if state=="collecting_pincode"
                      else _warm.get("silence",""))

    if state == "done":
        return static(_DONE)

    _HELLO = {"hello","helo","hlo","hi","haan ji","ji","sun raha","sun rahi","haan",
              "bol","bolo","boliye","ha","han","hmm","hm","are","arre","हेलो","जी",
              "हाँ जी","बोलिए","सुन रहा","सुन रही"}
    tl_stripped = t.lower().strip("?!., ")
    if tl_stripped in _HELLO or (len(t.split()) <= 2 and tl_stripped in _HELLO):
        _reask = {
            "permission":         _v(_GREET, cs["turn"]),
            "hair_problem":       _v(_ASK_HAIR, cs["turn"]),
            "pitch":              "जी — क्या आप आदिवासी हेयर ऑयल के बारे में जानना चाहते हैं?",
            "collecting_name":    _R_NAME,
            "collecting_city":    _R_CITY,
            "collecting_address": _R_ADDR,
            "collecting_pincode": _R_PIN,
            "confirming":         "जी — क्या दी गई जानकारी सही है?",
        }
        return static(_reask.get(state, "जी — बताइए।"))

    if is_off(t):
        return static(_OFFTOPIC)

    if state == "permission":
        if is_no(t):
            return static("कोई बात नहीं — बस 20 सेकंड। बालों में झड़ने या रूसी की समस्या है?",
                          next_state="hair_problem")
        return static(_v(_ASK_HAIR, cs["turn"]), next_state="hair_problem",
                      pre=_warm.get("hair",""))

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
            full = _v(_NO_PROBLEM, cs["turn"]) + " " + _URGENCY
            return static(full, next_state="pitch")
        else:
            cs["hair_problem"] = True
            full = _v(_YES_PROBLEM, cs["turn"]) + " हफ्ते में 2-3 बार लगाएं। सिर्फ 1499 — कैश ऑन डिलीवरी।"
            return static(full, next_state="pitch")

    if state == "pitch":
        if is_buy(t):
            return static(_v(_ASK_NAME, cs["turn"]), next_state="collecting_name",
                          pre=_warm.get("ask_name",""))
        if is_price_q(t):
            reply = _v(_PRICE_ANSWER, cs["turn"])
            return static(reply)
        # GPT path — pass user text, history is built inside gpt()
        return t, False, True, ""

    if state == "collecting_name":
        if len(t) >= 2 and "?" not in t:
            name = re.sub(
                r"^(mera naam|mera name|main|i am|naam hai|name is|मेरा नाम|मैं)\s+",
                "", t, flags=re.IGNORECASE
            ).strip().title()
            cs["name"] = name
            reply = f"{name} जी, " + _v(_ASK_CITY, cs["turn"])
            return static(reply, next_state="collecting_city",
                          pre=_warm.get("ask_city",""))
        return static(_R_NAME)

    if state == "collecting_city":
        if len(t) >= 2:
            cs["city"] = t.strip().title()
            return static(_v(_ASK_ADDR, cs["turn"]), next_state="collecting_address",
                          pre=_warm.get("ask_addr",""))
        return static(_R_CITY)

    if state == "collecting_address":
        if len(t) >= 5:
            cs["address"] = f"{t.strip()}, {cs['city']}"
            return static(_ASK_PIN, next_state="collecting_pincode",
                          pre=_warm.get("ask_pin",""))
        return static(_R_ADDR)

    if state == "collecting_pincode":
        pin = get_pin(t)
        print(f"📌 pin: '{t}' → '{pin}'")
        if pin:
            cs["pincode"] = pin
            cs["state"]   = "confirming"
            reply = (f"पुष्टि — नाम: {cs['name']}, शहर: {cs['city']}, "
                     f"पता: {cs['address']}, पिन: {pin}। क्या सही है?")
            cs["last_bot"] = reply
            return reply, False, False, ""
        return static(_R_PIN, pre=_warm.get("r_pin",""))

    if state == "confirming":
        if is_buy(t):
            cs["state"] = "done"
            asyncio.create_task(save_order(
                cs["name"], cs["address"], cs["pincode"], cs["caller"], cs.get("city","")
            ))
            reply = (f"बहुत धन्यवाद {cs['name']} जी! ऑर्डर दर्ज हो गया। "
                     f"5-7 दिन में {cs['city']} में डिलीवरी। 1499 रुपये डिलीवरी पर। शुभ हो!")
            cs["last_bot"] = reply
            return reply, True, False, ""
        if is_no(t):
            cs.update({"state":"collecting_name","name":"","city":"","address":"","pincode":""})
            return static("फिर से शुरू करते हैं। नाम बताइए।")
        return static("हाँ या नहीं बोलिए — जानकारी सही है?")

    # Fallback GPT
    return t, False, True, ""

# ═══════════════════════════════════════════════════
# GOOGLE SHEETS — ORDERS (Sheet1)
# ═══════════════════════════════════════════════════
async def save_order(name, address, pincode, phone, city=""):
    ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"📦 ORDER → {name} | {pincode} | {phone}")
    if not GOOGLE_SHEET_ID or not GOOGLE_CREDS_JSON:
        return
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _sheet_write, name, address, pincode, phone, ts)

def _sheet_write(name, address, pincode, phone, ts):
    try:
        svc = _build_sheets_service()
        if not svc: return
        svc.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range="Sheet1!A:H",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": [[ts, name, address, pincode, phone,
                              "Adivasi Hair Oil", "₹1499", "Pending"]]},
        ).execute()
        print(f"✅ Sheet1: {name} | {pincode}")
    except Exception as e:
        print(f"❌ Sheet1 error: {e}")

# ═══════════════════════════════════════════════════
# ISSUE1 + ISSUE3 FIX — TRANSCRIPT LOGGING
#
# ISSUE1: Previously user log and Priya log were two
# separate run_in_executor calls fired as background tasks.
# Each made its own Google API call = 2 slow calls per turn
# competing with the main call response thread pool.
# FIX: Single combined batch write — both rows in ONE API call.
# asyncio.sleep(0) yields event loop so call response goes first.
#
# ISSUE3: Two separate async tasks writing to Sheet2 had a
# race condition — whichever Google API call finished first
# appeared first in the sheet, breaking conversation order.
# FIX: Both user + Priya rows written in ONE atomic batch.
# Added turn_seq column so sheet can always be re-sorted correctly.
# ═══════════════════════════════════════════════════
async def log_turn(sid: str, caller: str, state: str,
                   user_text: str, priya_text: str):
    """
    Write BOTH user speech and Priya reply in a single Google API call.
    This fixes ISSUE1 (speed) and ISSUE3 (ordering) at the same time.
    asyncio.sleep(0) ensures the call response is sent to Twilio first.
    """
    await asyncio.sleep(0)  # yield event loop — call response goes out first
    if not GOOGLE_SHEET_ID or not GOOGLE_CREDS_JSON:
        return
    ts  = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cs  = _calls.get(sid, {})
    seq = cs.get("turn_seq", 0)
    cs["turn_seq"] = seq + 2          # reserve 2 sequence slots per turn

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None, _batch_transcript_write,
        ts, sid, caller, state, user_text, priya_text, seq
    )

def _batch_transcript_write(ts, sid, caller, state,
                             user_text, priya_text, seq):
    """
    Single Google Sheets API call writing both rows atomically.
    Columns: Seq | Timestamp | CallSid | Phone | Speaker | State | Message
    Sort by Seq column in Sheet2 to always get correct order.
    """
    try:
        svc = _build_sheets_service()
        if not svc: return
        rows = []
        if user_text:
            rows.append([seq,   ts, sid, caller, "User",  state, user_text])
        if priya_text:
            rows.append([seq+1, ts, sid, caller, "Priya", state, priya_text])
        if not rows:
            return
        svc.spreadsheets().values().append(
            spreadsheetId=GOOGLE_SHEET_ID,
            range="Sheet2!A:G",       # ← now 7 columns with Seq added
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()
    except Exception as e:
        print(f"❌ Sheet2 error: {e}")

# ═══════════════════════════════════════════════════
# TWILIO WEBHOOKS
# ═══════════════════════════════════════════════════
async def voice_start(request):
    try:    data = await request.post()
    except: data = {}
    sid    = data.get("CallSid","unknown")
    caller = data.get("From","unknown")
    _calls[sid] = new_cs(caller)
    print(f"📞 {sid} from {caller}")

    greeting = _GREET[0]
    pre = _warm.get("greet","")
    tw  = await mk_twiml(greeting, R(), pre_aid=pre)

    # Log greeting — user_text empty since no user has spoken yet
    asyncio.create_task(log_turn(sid, caller, "permission", "", greeting))
    return web.Response(text=tw, content_type="application/xml")

async def voice_respond(request):
    try:    data = await request.post()
    except: data = {}
    sid        = data.get("CallSid","unknown")
    caller     = data.get("From","unknown")
    speech     = data.get("SpeechResult","").strip()
    confidence = float(data.get("Confidence","0") or "0")
    no_speech  = request.rel_url.query.get("ns","0")

    print(f"🗣  [{sid}] '{speech}' conf={confidence:.2f}"
          + (" ⚠️LOW" if 0 < confidence < 0.4 else "")
          + (" 🔇EMPTY" if not speech else ""))

    # ── ISSUE2 FIX: low confidence → ask to repeat, don't guess ──────────
    # If confidence < 0.5 and speech is very short (1-2 words),
    # the STT likely got it wrong — ask customer to repeat clearly.
    if speech and confidence > 0 and confidence < 0.5 and len(speech.split()) <= 3:
        cs    = _calls.get(sid, new_cs(caller))
        state = cs.get("state", "permission")
        # Only apply in pitch state where wrong replies are most harmful
        if state == "pitch":
            print(f"⚠️  Low conf in pitch state — asking clarification")
            tw = await mk_twiml(_LOW_CONF, R(), pre_aid=_warm.get("low_conf",""))
            asyncio.create_task(log_turn(sid, caller, state, speech, _LOW_CONF))
            return web.Response(text=tw, content_type="application/xml")

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
        tw = await mk_twiml(msg, R())
        asyncio.create_task(log_turn(sid, caller, state, "", msg))
        return web.Response(text=tw, content_type="application/xml")

    current_state = _calls.get(sid, {}).get("state", "unknown")

    # Process and get reply
    result = await process(sid, speech, caller)
    reply_text, hangup, use_gpt, pre_aid = result

    cs = _calls.get(sid, {})

    if use_gpt:
        gpt_text = await gpt(cs, speech)
        cs["last_bot"] = gpt_text
        # ISSUE2 FIX: update conversation history for next GPT call
        cs.setdefault("history", []).append({"user": speech, "bot": gpt_text})
        if len(cs["history"]) > 3:
            cs["history"] = cs["history"][-3:]
        tw = await mk_twiml(gpt_text, R(), hangup=hangup)
        # ISSUE1+3 FIX: single combined log call
        asyncio.create_task(log_turn(
            sid, caller, cs.get("state","unknown"), speech, gpt_text
        ))
    else:
        # ISSUE2 FIX: update history for static replies too
        cs.setdefault("history", []).append({"user": speech, "bot": reply_text})
        if len(cs["history"]) > 3:
            cs["history"] = cs["history"][-3:]
        tw = await mk_twiml(reply_text, R(), hangup=hangup, pre_aid=pre_aid)
        # ISSUE1+3 FIX: single combined log call
        asyncio.create_task(log_turn(
            sid, caller, cs.get("state","unknown"), speech, reply_text
        ))

    final_reply = gpt_text if use_gpt else reply_text
    print(f"🤖 [{cs.get('state','?')}] {final_reply[:80]}")
    return web.Response(text=tw, content_type="application/xml")

# ═══════════════════════════════════════════════════
# KEEP-ALIVE
# ═══════════════════════════════════════════════════
async def keepalive():
    await asyncio.sleep(60)
    while True:
        try:
            s = await http()
            async with s.get(f"{PUBLIC_URL}/", timeout=aiohttp.ClientTimeout(total=8)) as r:
                print(f"🏓 {r.status}")
        except Exception as e:
            print(f"⚠️  keepalive: {e}")
        await asyncio.sleep(480)

async def on_startup(app):
    _build_sheets_service()
    asyncio.create_task(keepalive())
    asyncio.create_task(prewarm())

async def on_cleanup(app):
    global _http
    if _http and not _http.closed:
        await _http.close()
        print("✅ aiohttp closed")

async def health(request):
    return web.json_response({
        "ok": True, "product": "Adivasi Hair Oil",
        "version": "v4",
        "sarvam": bool(SARVAM_API_KEY), "sheet": bool(GOOGLE_SHEET_ID),
        "calls": len(_calls), "cached_audio": len(_ac),
        "prewarmed": list(_warm.keys()),
        "sheets_svc_ready": _sheets_svc is not None,
        "transcript": "Sheet2 (7 cols incl Seq for ordering)",
        "fixes": ["issue1:batch_log", "issue2:history+low_conf", "issue3:atomic_write"],
    })

def create_app():
    app = web.Application(client_max_size=8 * 1024 * 1024)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    app.router.add_get("/",               health)
    app.router.add_post("/voice/start",   voice_start)
    app.router.add_post("/voice/respond", voice_respond)
    app.router.add_get("/audio/{aid}",    audio_serve)
    return app

if __name__ == "__main__":
    print("═"*55)
    print("  🌿 Priya — Adivasi Hair Oil | v4 — 3 issues fixed")
    print(f"  {PUBLIC_URL}  |  :{PORT}")
    print(f"  TTS        {'✅ Sarvam' if SARVAM_API_KEY else '⚠️  Polly.Kajal'}")
    print(f"  Orders     {'✅ Sheet1' if GOOGLE_SHEET_ID else '⚠️  logs only'}")
    print(f"  Transcript {'✅ Sheet2 (batch atomic)' if GOOGLE_SHEET_ID else '⚠️  logs only'}")
    print(f"  Pre-warm   ✅ {len(_PREWARM_MAP)} replies at startup")
    print(f"  GPT ctx    ✅ last 3 turns as history")
    print(f"  Low conf   ✅ clarification prompt in pitch state")
    print("═"*55)
    web.run_app(create_app(), host="0.0.0.0", port=PORT)
