"""
Initial values for every drop-down list and the starting masters (job types, crops, fertilizers).

This is the ONLY place where starting values are written down. After installation all of them
are ordinary database rows that can be renamed, translated, deactivated or extended from
Setup > Drop-down lists and the master screens.

`load_initial_data(apps)` is also used to refill a freshly emptied demo database.
"""
from decimal import Decimal as D

from django.db import migrations

# (English, Malayalam, Hindi, Tamil, extra fields)
LOOKUPS = {
    "Gender": [
        ("Male", "ആൺ", "पुरुष", "ஆண்", {}),
        ("Female", "പെൺ", "महिला", "பெண்", {}),
        ("Other", "മറ്റുള്ളവ", "अन्य", "மற்றவை", {}),
    ],
    "EmploymentType": [
        ("Permanent", "സ്ഥിരം", "स्थायी", "நிரந்தரம்", {}),
        ("Temporary / Casual", "താൽക്കാലികം", "अस्थायी", "தற்காலிகம்", {}),
        ("Contract", "കരാർ", "अनुबंध", "ஒப்பந்தம்", {}),
        ("Seasonal (Harvest)", "സീസണൽ (വിളവെടുപ്പ്)", "मौसमी (फसल)", "பருவகால (அறுவடை)", {}),
        ("Migrant worker", "അതിഥി തൊഴിലാളി", "प्रवासी मज़दूर", "புலம்பெயர் தொழிலாளர்", {}),
    ],
    "JobCategory": [
        ("Harvesting / Picking", "വിളവെടുപ്പ് / പറിക്കൽ", "कटाई / तुड़ाई", "அறுவடை / பறித்தல்", {}),
        ("Manuring / Fertilizing", "വളപ്രയോഗം", "खाद डालना", "உரமிடுதல்", {}),
        ("Spraying (plant protection)", "മരുന്നടി (സസ്യസംരക്ഷണം)", "छिड़काव (पौध संरक्षण)",
         "தெளித்தல் (பயிர் பாதுகாப்பு)", {}),
        ("Weeding / Slashing", "കള പറിക്കൽ / വെട്ടൽ", "निराई / झाड़ी कटाई", "களை எடுத்தல் / வெட்டுதல்", {}),
        ("Trashing / Pruning", "ട്രാഷിംഗ് / കൊമ്പുകോതൽ", "छंटाई", "கவாத்து / சுத்தம் செய்தல்", {}),
        ("Irrigation", "ജലസേചനം", "सिंचाई", "நீர்ப்பாசனம்", {}),
        ("Planting / Gap filling", "നടീൽ / വിടവ് നികത്തൽ", "रोपाई / खाली जगह भरना", "நடவு / இடைவெளி நிரப்புதல்", {}),
        ("Processing / Drying / Curing", "സംസ്കരണം / ഉണക്കൽ", "प्रसंस्करण / सुखाना", "பதப்படுத்தல் / உலர்த்துதல்", {}),
        ("Supervision / Management", "മേൽനോട്ടം / മാനേജ്മെന്റ്", "पर्यवेक्षण / प्रबंधन", "மேற்பார்வை / நிர்வாகம்", {}),
        ("Maintenance / General", "അറ്റകുറ്റപ്പണി / പൊതു", "रखरखाव / सामान्य", "பராமரிப்பு / பொது", {}),
        ("Driving / Transport", "ഡ്രൈവിംഗ് / ഗതാഗതം", "ड्राइविंग / परिवहन", "ஓட்டுநர் / போக்குவரத்து", {}),
        ("Other", "മറ്റുള്ളവ", "अन्य", "மற்றவை", {}),
    ],
    "CropRole": [
        ("Main crop", "പ്രധാന വിള", "मुख्य फसल", "முதன்மைப் பயிர்", {}),
        ("Inter crop", "ഇടവിള", "अंतर-फसल", "ஊடுபயிர்", {}),
        ("Other produce", "മറ്റ് ഉൽപ്പന്നങ്ങൾ", "अन्य उपज", "பிற விளைபொருள்", {}),
    ],
    "Unit": [
        ("kg", "കിലോ", "किलो", "கிலோ", {}),
        ("gram", "ഗ്രാം", "ग्राम", "கிராம்", {}),
        ("litre", "ലിറ്റർ", "लीटर", "லிட்டர்", {}),
        ("ml", "മി.ലി.", "मि.ली.", "மி.லி.", {}),
        ("bag", "ചാക്ക്", "बोरी", "மூட்டை", {}),
        ("nos", "എണ്ണം", "नग", "எண்ணிக்கை", {}),
        ("quintal", "ക്വിന്റൽ", "क्विंटल", "குவிண்டால்", {}),
        ("tonne", "ടൺ", "टन", "டன்", {}),
    ],
    "InputType": [
        ("Chemical fertilizer", "രാസവളം", "रासायनिक उर्वरक", "இரசாயன உரம்", {}),
        ("Organic manure / Compost", "ജൈവവളം / കമ്പോസ്റ്റ്", "जैविक खाद / कम्पोस्ट", "இயற்கை உரம் / மட்கு உரம்", {}),
        ("Micronutrient", "സൂക്ഷ്മ മൂലകം", "सूक्ष्म पोषक तत्व", "நுண்ணூட்டச்சத்து", {}),
        ("Insecticide", "കീടനാശിനി", "कीटनाशक", "பூச்சிக்கொல்லி", {}),
        ("Fungicide", "കുമിൾനാശിനി", "फफूंदनाशक", "பூஞ்சைக்கொல்லி", {}),
        ("Herbicide / Weedicide", "കളനാശിനി", "खरपतवारनाशक", "களைக்கொல்லி", {}),
        ("Bio-agent", "ജൈവ നിയന്ത്രണ ഉപാധി", "जैव एजेंट", "உயிரியல் காரணி", {}),
        ("Lime / Dolomite", "കുമ്മായം / ഡോളമൈറ്റ്", "चूना / डोलोमाइट", "சுண்ணாம்பு / டோலமைட்", {}),
        ("Growth regulator / Tonic", "വളർച്ചാ നിയന്ത്രകം / ടോണിക്", "वृद्धि नियामक / टॉनिक", "வளர்ச்சி ஊக்கி / டானிக்", {}),
        ("Other", "മറ്റുള്ളവ", "अन्य", "மற்றவை", {}),
    ],
    "AttendanceStatus": [
        ("Full day", "മുഴുവൻ ദിവസം", "पूरा दिन", "முழு நாள்", {"pay_fraction": D("1"), "color": "#198754"}),
        ("Half day", "അര ദിവസം", "आधा दिन", "அரை நாள்", {"pay_fraction": D("0.5"), "color": "#fd7e14"}),
        ("Leave", "അവധി (ലീവ്)", "छुट्टी", "விடுப்பு", {"is_leave": True, "color": "#0d6efd"}),
        ("Absent", "ഹാജരില്ല", "अनुपस्थित", "வரவில்லை", {"color": "#dc3545"}),
        ("Holiday", "ഒഴിവുദിനം", "अवकाश दिवस", "விடுமுறை நாள்", {"color": "#6c757d"}),
    ],
    "PaymentMode": [
        ("Cash", "പണം", "नकद", "ரொக்கம்", {}),
        ("Bank transfer", "ബാങ്ക് ട്രാൻസ്ഫർ", "बैंक ट्रांसफर", "வங்கி பரிமாற்றம்", {}),
        ("UPI", "യുപിഐ", "यूपीआई", "யுபிஐ", {}),
        ("Cheque", "ചെക്ക്", "चेक", "காசோலை", {}),
    ],
    "PaymentStatus": [
        ("Pending", "നൽകാനുണ്ട്", "लंबित", "நிலுவையில்", {}),
        ("Paid", "നൽകി", "भुगतान हो गया", "செலுத்தப்பட்டது", {"is_final": True}),
    ],
    "ReceiptStatus": [
        ("Received", "ലഭിച്ചു", "प्राप्त", "பெறப்பட்டது", {}),
        ("Pending", "ലഭിക്കാനുണ്ട്", "बाकी", "நிலுவையில்", {}),
        ("Partially received", "ഭാഗികമായി ലഭിച്ചു", "आंशिक प्राप्त", "பகுதியாக பெறப்பட்டது", {}),
    ],
    "ApplicationMethod": [
        ("Soil application", "മണ്ണിൽ ചേർക്കൽ", "मिट्टी में डालना", "மண்ணில் இடுதல்", {}),
        ("Foliar spray", "ഇലയിൽ തളിക്കൽ", "पत्तियों पर छिड़काव", "இலைவழி தெளிப்பு", {}),
        ("Soil drench", "മണ്ണിൽ ഒഴിക്കൽ", "मिट्टी में घोल डालना", "மண்ணில் ஊற்றுதல்", {}),
        ("Fertigation / Drip", "ഫെർട്ടിഗേഷൻ / ഡ്രിപ്പ്", "फर्टिगेशन / ड्रिप", "உரப்பாசனம் / சொட்டு நீர்", {}),
        ("Manual work", "കൈകൊണ്ടുള്ള പണി", "हाथ से काम", "கைமுறை வேலை", {}),
        ("Other", "മറ്റുള്ളവ", "अन्य", "மற்றவை", {}),
    ],
    "ProcessType": [
        ("Drying / Curing", "ഉണക്കൽ / ക്യൂറിംഗ്", "सुखाना / क्योरिंग", "உலர்த்துதல் / பதப்படுத்துதல்", {}),
        ("Pulping / Washing", "പൾപ്പിംഗ് / കഴുകൽ", "पल्पिंग / धुलाई", "கூழாக்குதல் / கழுவுதல்", {}),
        ("Grading / Sorting", "ഗ്രേഡിംഗ് / തരംതിരിക്കൽ", "ग्रेडिंग / छंटाई", "தரம் பிரித்தல்", {}),
        ("Polishing", "പോളിഷിംഗ്", "पॉलिशिंग", "மெருகூட்டல்", {}),
        ("Hulling", "തൊലി കളയൽ", "छिलका उतारना", "உமி நீக்குதல்", {}),
        ("Other", "മറ്റുള്ളവ", "अन्य", "மற்றவை", {}),
    ],
    "ExpenseCategory": [
        ("Fertilizer / Chemical purchase", "വളം / മരുന്ന് വാങ്ങൽ", "उर्वरक / रसायन ख़रीद", "உரம் / மருந்து கொள்முதல்", {}),
        ("Fuel (dryer / vehicle)", "ഇന്ധനം (ഡ്രയർ / വാഹനം)", "ईंधन (ड्रायर / वाहन)", "எரிபொருள் (உலர்த்தி / வாகனம்)", {}),
        ("Electricity", "വൈദ്യുതി", "बिजली", "மின்சாரம்", {}),
        ("Equipment / Tools", "ഉപകരണങ്ങൾ", "उपकरण / औज़ार", "உபகரணங்கள் / கருவிகள்", {}),
        ("Repairs & Maintenance", "അറ്റകുറ്റപ്പണി", "मरम्मत व रखरखाव", "பழுது நீக்கம் & பராமரிப்பு", {}),
        ("Transport", "ഗതാഗതം", "परिवहन", "போக்குவரத்து", {}),
        ("Irrigation", "ജലസേചനം", "सिंचाई", "நீர்ப்பாசனம்", {}),
        ("Planting material / Seedlings", "നടീൽ വസ്തു / തൈകൾ", "रोपण सामग्री / पौध", "நடவுப் பொருள் / நாற்றுகள்", {}),
        ("Auction / Marketing charges", "ലേലം / വിപണന ചെലവ്", "नीलामी / विपणन शुल्क",
         "ஏலம் / சந்தைப்படுத்தல் கட்டணம்", {}),
        ("Labour welfare / Housing", "തൊഴിലാളി ക്ഷേമം / താമസം", "श्रमिक कल्याण / आवास", "தொழிலாளர் நலன் / குடியிருப்பு", {}),
        ("Tax / Lease / Licence", "നികുതി / പാട്ടം / ലൈസൻസ്", "कर / पट्टा / लाइसेंस", "வரி / குத்தகை / உரிமம்", {}),
        ("Other", "മറ്റുള്ളവ", "अन्य", "மற்றவை", {}),
    ],
}

# Field operations: last item = name of their usual fertilizer / chemical type
OPERATION_TYPES = [
    ("Fertilizer application", "വളപ്രയോഗം", "उर्वरक प्रयोग", "உரமிடுதல்", "Chemical fertilizer"),
    ("Organic manure / Compost", "ജൈവവള പ്രയോഗം", "जैविक खाद प्रयोग", "இயற்கை உரமிடுதல்", "Organic manure / Compost"),
    ("Micronutrient application", "സൂക്ഷ്മ മൂലക പ്രയോഗം", "सूक्ष्म पोषक प्रयोग", "நுண்ணூட்டச்சத்து இடுதல்", "Micronutrient"),
    ("Insecticide spray", "കീടനാശിനി തളിക്കൽ", "कीटनाशक छिड़काव", "பூச்சிக்கொல்லி தெளிப்பு", "Insecticide"),
    ("Fungicide spray / drench", "കുമിൾനാശിനി പ്രയോഗം", "फफूंदनाशक छिड़काव", "பூஞ்சைக்கொல்லி தெளிப்பு", "Fungicide"),
    ("Herbicide / Weedicide", "കളനാശിനി പ്രയോഗം", "खरपतवारनाशक छिड़काव", "களைக்கொல்லி தெளிப்பு", "Herbicide / Weedicide"),
    ("Bio-agent application", "ജൈവ നിയന്ത്രണ പ്രയോഗം", "जैव एजेंट प्रयोग", "உயிரியல் காரணி இடுதல்", "Bio-agent"),
    ("Liming", "കുമ്മായം ചേർക്കൽ", "चूना डालना", "சுண்ணாம்பு இடுதல்", "Lime / Dolomite"),
    ("Manual weeding / Slashing", "കള പറിക്കൽ", "निराई", "களை எடுத்தல்", None),
    ("Trashing / Pruning", "ട്രാഷിംഗ് / കൊമ്പുകോതൽ", "छंटाई", "கவாத்து", None),
    ("Mulching", "പുതയിടൽ", "मल्चिंग", "மூடாக்கு இடுதல்", None),
    ("Irrigation", "ജലസേചനം", "सिंचाई", "நீர்ப்பாசனம்", None),
    ("Shade regulation", "തണൽ ക്രമീകരണം", "छाया नियंत्रण", "நிழல் ஒழுங்குபடுத்தல்", None),
    ("Planting / Gap filling", "നടീൽ / വിടവ് നികത്തൽ", "रोपाई / खाली जगह भरना", "நடவு / இடைவெளி நிரப்புதல்", None),
    ("Other", "മറ്റുള്ളവ", "अन्य", "மற்றவை", None),
]

# name, ml, hi, ta, category, default daily wage, default OT rate / hour
JOB_TYPES = [
    ("Cardamom Picking", "ഏലം പറിക്കൽ", "इलायची तुड़ाई", "ஏலக்காய் பறித்தல்", "Harvesting / Picking", 500, 70),
    ("Pepper Harvesting", "കുരുമുളക് പറിക്കൽ", "काली मिर्च तुड़ाई", "மிளகு அறுவடை", "Harvesting / Picking", 550, 75),
    ("Coffee Picking", "കാപ്പി പറിക്കൽ", "कॉफ़ी तुड़ाई", "காபி பறித்தல்", "Harvesting / Picking", 500, 70),
    ("Manuring", "വളമിടൽ", "खाद डालना", "உரமிடுதல்", "Manuring / Fertilizing", 550, 75),
    ("Spraying", "മരുന്നടി", "छिड़काव", "மருந்து தெளித்தல்", "Spraying (plant protection)", 650, 90),
    ("Weeding", "കള പറിക്കൽ", "निराई", "களை எடுத்தல்", "Weeding / Slashing", 500, 70),
    ("Trashing / Pruning", "ട്രാഷിംഗ് / കൊമ്പുകോതൽ", "छंटाई", "கவாத்து", "Trashing / Pruning", 550, 75),
    ("Drying / Curing", "ഉണക്കൽ", "सुखाना", "உலர்த்துதல்", "Processing / Drying / Curing", 600, 85),
    ("Irrigation", "ജലസേചനം", "सिंचाई", "நீர்ப்பாசனம்", "Irrigation", 550, 75),
    ("Supervisor", "സൂപ്പർവൈസർ", "पर्यवेक्षक", "மேற்பார்வையாளர்", "Supervision / Management", 900, 120),
    ("Driver", "ഡ്രൈവർ", "ड्राइवर", "ஓட்டுநர்", "Driving / Transport", 750, 100),
    ("General Work", "പൊതു പണി", "सामान्य काम", "பொது வேலை", "Maintenance / General", 500, 70),
]

PRODUCTS = [
    {"name": "Cardamom", "name_ml": "ഏലം", "name_hi": "इलायची", "name_ta": "ஏலக்காய்", "crop_role": "Main crop",
     "unit": "kg", "raw_form": "Green capsules", "processed_form": "Dry cardamom", "typical_recovery_percent": D("21"),
     "typical_rate": D("2400"), "harvest_months": "8,9,10,11,12,1,2", "harvest_job_type": "Cardamom Picking"},
    {"name": "Pepper", "name_ml": "കുരുമുളക്", "name_hi": "काली मिर्च", "name_ta": "மிளகு", "crop_role": "Inter crop",
     "unit": "kg", "raw_form": "Green berries", "processed_form": "Black pepper", "typical_recovery_percent": D("33"),
     "typical_rate": D("640"), "harvest_months": "12,1,2,3", "harvest_job_type": "Pepper Harvesting"},
    {"name": "Coffee (Robusta)", "name_ml": "കാപ്പി (റോബസ്റ്റ)", "name_hi": "कॉफ़ी (रोबस्टा)",
     "name_ta": "காபி (ரோபஸ்டா)", "crop_role": "Inter crop", "unit": "kg", "raw_form": "Fresh cherry",
     "processed_form": "Dry cherry", "typical_recovery_percent": D("47"), "typical_rate": D("210"),
     "harvest_months": "12,1,2", "harvest_job_type": "Coffee Picking"},
]

# name, type, composition, unit, cost per unit
AGRI_INPUTS = [
    ("NPK 18:18:18", "Chemical fertilizer", "NPK 18:18:18", "kg", 95),
    ("Urea", "Chemical fertilizer", "46% N", "kg", 7),
    ("Muriate of Potash (MOP)", "Chemical fertilizer", "60% K2O", "kg", 36),
    ("Rock Phosphate", "Chemical fertilizer", "P2O5 20%", "kg", 12),
    ("Dolomite", "Lime / Dolomite", "CaMg(CO3)2", "kg", 6),
    ("Neem Cake", "Organic manure / Compost", "Neem seed cake", "kg", 32),
    ("Cow dung compost", "Organic manure / Compost", "", "kg", 4),
    ("Spinosad 45 SC", "Insecticide", "Spinosad 45% SC", "ml", 18),
    ("Copper Oxychloride 50 WP", "Fungicide", "COC 50% WP", "kg", 650),
    ("Bordeaux mixture", "Fungicide", "1% Bordeaux", "kg", 120),
    ("Trichoderma", "Bio-agent", "T. harzianum", "kg", 120),
    ("Micronutrient mix", "Micronutrient", "Zn, B, Mg", "kg", 140),
]


def load_initial_data(apps, schema_editor=None, using=None):
    # write to the database being migrated (the real one or the demo one)
    db = using or (schema_editor.connection.alias if schema_editor else "default")

    def objects(name):
        return apps.get_model("estate", name).objects.db_manager(db)

    def rows(name, items):
        objs = {}
        for i, (en, ml, hi, ta, extra) in enumerate(items, start=1):
            objs[en], _ = objects(name).get_or_create(
                name=en, defaults={"name_ml": ml, "name_hi": hi, "name_ta": ta, "sort_order": i * 10, **extra})
        return objs

    lk = {name: rows(name, items) for name, items in LOOKUPS.items()}
    for i, (en, ml, hi, ta, input_type) in enumerate(OPERATION_TYPES, start=1):
        objects("OperationType").get_or_create(
            name=en, defaults={"name_ml": ml, "name_hi": hi, "name_ta": ta, "sort_order": i * 10,
                               "input_type": lk["InputType"].get(input_type)})
    jobs = {}
    for en, ml, hi, ta, cat, wage, ot in JOB_TYPES:
        jobs[en], _ = objects("JobType").get_or_create(
            name=en, defaults={"name_ml": ml, "name_hi": hi, "name_ta": ta, "category": lk["JobCategory"][cat],
                               "default_daily_wage": D(wage), "default_ot_rate": D(ot)})
    for p in PRODUCTS:
        p = dict(p)
        p["crop_role"] = lk["CropRole"][p["crop_role"]]
        p["unit"] = lk["Unit"][p["unit"]]
        p["harvest_job_type"] = jobs[p["harvest_job_type"]]
        name = p.pop("name")
        objects("Product").get_or_create(name=name, defaults=p)
    for name, typ, comp, unit, cost in AGRI_INPUTS:
        objects("AgriInput").get_or_create(
            name=name, defaults={"input_type": lk["InputType"][typ], "composition": comp,
                                 "unit": lk["Unit"][unit], "unit_cost": D(cost)})


class Migration(migrations.Migration):
    dependencies = [("estate", "0001_initial")]

    operations = [migrations.RunPython(load_initial_data, migrations.RunPython.noop)]
