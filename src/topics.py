"""Topics tracked across statements, labels and mood: полонені, обміни, перемир'я, ТЦК і мобілізація, корупція, закони.
TOPIC_RX is a Postgres regex (~*) used to pick candidate posts; the model assigns the topic itself. Keep the classes in
sync with topic_of() in db/migrations/020_topics_mood.sql (same words; that function also orders them)."""

TOPICS = ("pow", "exchange", "ceasefire", "tcc", "law", "corruption")
_CLASSES = {
    "exchange": r"обмін\w* полонен|обмен\w* (военно)?пленн|обмін\w* тіл|обмен\w* тел|prisoner (swap|exchange)",
    "pow": r"полонен|\mполоні\M|військовополон|военнопл|\mпленн|\mв плену|\mплен(ные|ных|ным|ными)\M|prisoners? of war|\mPOWs?\M",
    "ceasefire": r"перемир|прекращени\w* огня|припинен\w* вогню|режим\w* тиш|ceasefire|truce|мораторі|моратори",
    "tcc": r"\mТЦК|військкомат|военкомат|мобілізац|мобилизац|повістк|\mповестк(а|и|у|ой|ами|ам)?\M(?!\s+дн)|бусифік|ухилянт|Резерв\+|"
           r"відстрочк|отсрочк|демобіліз|демобилиз",
    "corruption": r"\mНАБУ\M|\mСАП\M|\mВАКС\M|\mНАЗК\M|корупц|коррупц|хабар(?!овс)|взятк|розкрада|розтрат",
    "law": r"законопроєкт|законопроект|Верховн\w* Рад|\mВРУ\M|ухвалил\w* закон|підписав закон|ветував|постанов\w* (КМУ|Кабмін|уряду)|"
           r"указ президента|закон про",
}
TOPIC_RX = "(" + "|".join(_CLASSES.values()) + ")"
