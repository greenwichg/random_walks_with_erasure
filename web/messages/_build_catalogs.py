#!/usr/bin/env python3
"""Generate web/messages/{en,es,fr,de,pt}.json from one source so all five share identical keys.

Each entry maps a dot-key to its five translations (en, es, fr, de, pt). Machine-grade non-English
strings, flagged in the implementation report for later native review. Interpolation uses {name}."""
import json
import pathlib

OUT = pathlib.Path(__file__).resolve().parent
OUT.mkdir(parents=True, exist_ok=True)

# key: (en, es, fr, de, pt)
M = {
    # -------- common (buttons / status) --------
    "common.save": ("Save", "Guardar", "Enregistrer", "Speichern", "Salvar"),
    "common.saving": ("Saving…", "Guardando…", "Enregistrement…", "Speichern…", "Salvando…"),
    "common.allChangesSaved": ("All changes saved", "Todos los cambios guardados",
                               "Toutes les modifications enregistrées", "Alle Änderungen gespeichert",
                               "Todas as alterações salvas"),
    "common.cancel": ("Cancel", "Cancelar", "Annuler", "Abbrechen", "Cancelar"),
    "common.retry": ("Retry", "Reintentar", "Réessayer", "Erneut versuchen", "Tentar novamente"),
    "common.tryAgain": ("Try again", "Inténtalo de nuevo", "Réessayer", "Erneut versuchen",
                        "Tente novamente"),
    "common.loading": ("Loading…", "Cargando…", "Chargement…", "Wird geladen…", "Carregando…"),
    "common.readArticle": ("Read article", "Leer artículo", "Lire l'article", "Artikel lesen",
                           "Ler artigo"),
    "common.save_v": ("Save", "Guardar", "Enregistrer", "Speichern", "Salvar"),
    # -------- navigation --------
    "nav.dashboard": ("Dashboard", "Panel", "Tableau de bord", "Übersicht", "Painel"),
    "nav.report": ("Health Report", "Informe de salud", "Rapport de santé", "Gesundheitsbericht",
                   "Relatório de saúde"),
    "nav.recommendations": ("Recommendations", "Recomendaciones", "Recommandations", "Empfehlungen",
                            "Recomendações"),
    "nav.coach": ("AI Coach", "Asistente IA", "Coach IA", "KI-Coach", "Assistente de IA"),
    "nav.discover": ("Discover", "Descubrir", "Découvrir", "Entdecken", "Descobrir"),
    "nav.stories": ("Stories", "Noticias", "Sujets", "Themen", "Histórias"),
    "nav.saved": ("Saved", "Guardados", "Enregistrés", "Gespeichert", "Salvos"),
    "nav.history": ("Reading History", "Historial de lectura", "Historique de lecture",
                    "Leseverlauf", "Histórico de leitura"),
    "nav.analytics": ("Analytics", "Analíticas", "Analyses", "Analysen", "Análises"),
    "nav.profile": ("Profile", "Perfil", "Profil", "Profil", "Perfil"),
    "nav.settings": ("Settings", "Ajustes", "Paramètres", "Einstellungen", "Configurações"),
    "nav.section.explore": ("Explore", "Explorar", "Explorer", "Erkunden", "Explorar"),
    "nav.section.account": ("Account", "Cuenta", "Compte", "Konto", "Conta"),
    "nav.hint.dashboard": ("Your health at a glance", "Tu salud de un vistazo",
                           "Votre santé en un coup d'œil", "Ihre Gesundheit auf einen Blick",
                           "Sua saúde num relance"),
    "nav.hint.report": ("The full reading-diet analysis", "El análisis completo de tu dieta de lectura",
                        "L'analyse complète de votre régime de lecture",
                        "Die vollständige Analyse Ihrer Lesediät",
                        "A análise completa da sua dieta de leitura"),
    "nav.hint.recommendations": ("Reads picked to balance your diet",
                                 "Lecturas para equilibrar tu dieta",
                                 "Des lectures pour équilibrer votre régime",
                                 "Lektüre zum Ausgleich Ihrer Diät",
                                 "Leituras para equilibrar sua dieta"),
    "nav.hint.coach": ("Ask about your reading", "Pregunta sobre tu lectura",
                       "Posez des questions sur vos lectures", "Fragen Sie zu Ihrer Lektüre",
                       "Pergunte sobre sua leitura"),
    "nav.hint.discover": ("Trending stories & clusters", "Noticias y grupos en tendencia",
                          "Sujets et groupes tendance", "Trends & Cluster",
                          "Histórias e grupos em alta"),
    "nav.hint.stories": ("One event, every viewpoint", "Un evento, todos los puntos de vista",
                         "Un événement, tous les points de vue", "Ein Ereignis, jede Sichtweise",
                         "Um evento, todos os pontos de vista"),
    "nav.hint.saved": ("Articles you saved to read later", "Artículos guardados para leer después",
                       "Articles enregistrés pour plus tard", "Artikel für später gespeichert",
                       "Artigos salvos para ler depois"),
    "nav.hint.history": ("Everything you've read", "Todo lo que has leído",
                         "Tout ce que vous avez lu", "Alles, was Sie gelesen haben",
                         "Tudo o que você leu"),
    "nav.hint.analytics": ("Trends over time", "Tendencias a lo largo del tiempo",
                           "Tendances dans le temps", "Trends im Zeitverlauf",
                           "Tendências ao longo do tempo"),
    # -------- dashboard --------
    "dashboard.title": ("Dashboard", "Panel", "Tableau de bord", "Übersicht", "Painel"),
    "dashboard.subtitle": ("Your Information Health at a glance.",
                           "Tu salud informativa de un vistazo.",
                           "Votre santé informationnelle en un coup d'œil.",
                           "Ihre Informationsgesundheit auf einen Blick.",
                           "Sua saúde informacional num relance."),
    "dashboard.today": ("Today's reading", "Lectura de hoy", "Lecture du jour",
                        "Heutige Lektüre", "Leitura de hoje"),
    "dashboard.articlesRead": ("Articles read", "Artículos leídos", "Articles lus",
                               "Gelesene Artikel", "Artigos lidos"),
    "dashboard.minutesRead": ("Reading time", "Tiempo de lectura", "Temps de lecture", "Lesezeit",
                              "Tempo de leitura"),
    "dashboard.readingGoal": ("Reading goal", "Meta de lectura", "Objectif de lecture", "Leseziel",
                              "Meta de leitura"),
    "dashboard.streak": ("Reading streak", "Racha de lectura", "Série de lecture", "Lese-Serie",
                         "Sequência de leitura"),
    "dashboard.goalMet": ("Goal met", "Meta cumplida", "Objectif atteint", "Ziel erreicht",
                          "Meta atingida"),
    "dashboard.avgReadingTime": ("Avg. reading time", "Tiempo medio de lectura",
                                 "Temps de lecture moyen", "Ø Lesezeit", "Tempo médio de leitura"),
    "dashboard.ofGoal": ("of {min} min goal", "de {min} min de meta", "sur {min} min d'objectif",
                         "von {min} Min. Ziel", "de {min} min de meta"),
    "dashboard.politicalReading": ("Political reading", "Lectura política", "Lecture politique",
                                   "Politische Lektüre", "Leitura política"),
    "dashboard.days": ("days", "días", "jours", "Tage", "dias"),
    "dashboard.topTopics": ("Top topics today:", "Temas destacados hoy:",
                            "Sujets du jour :", "Top-Themen heute:", "Principais temas hoje:"),
    "dashboard.healthMetrics": ("Your health metrics", "Tus indicadores de salud",
                                "Vos indicateurs de santé", "Ihre Gesundheitskennzahlen",
                                "Seus indicadores de saúde"),
    # -------- settings --------
    "settings.title": ("Settings", "Ajustes", "Paramètres", "Einstellungen", "Configurações"),
    "settings.subtitle": ("Tune the app to your reading.", "Ajusta la app a tu lectura.",
                          "Adaptez l'app à votre lecture.", "Passen Sie die App an Ihre Lektüre an.",
                          "Ajuste o app à sua leitura."),
    "settings.appearance": ("Appearance", "Apariencia", "Apparence", "Darstellung", "Aparência"),
    "settings.theme": ("Theme", "Tema", "Thème", "Design", "Tema"),
    "settings.themeDesc": ("Light, dark, or match your system.",
                           "Claro, oscuro o según tu sistema.",
                           "Clair, sombre ou selon votre système.",
                           "Hell, dunkel oder wie Ihr System.",
                           "Claro, escuro ou conforme o sistema."),
    "settings.theme.light": ("Light", "Claro", "Clair", "Hell", "Claro"),
    "settings.theme.dark": ("Dark", "Oscuro", "Sombre", "Dunkel", "Escuro"),
    "settings.theme.system": ("System", "Sistema", "Système", "System", "Sistema"),
    "settings.language": ("Language", "Idioma", "Langue", "Sprache", "Idioma"),
    # truthfulness fix (#8): describe only implemented behaviour — no "digests"
    "settings.languageDesc": ("The language for the app interface.",
                              "El idioma de la interfaz de la app.",
                              "La langue de l'interface de l'application.",
                              "Die Sprache der App-Oberfläche.",
                              "O idioma da interface do app."),
    "settings.recommendations": ("Recommendations", "Recomendaciones", "Recommandations",
                                 "Empfehlungen", "Recomendações"),
    "settings.politicalOpenness": ("Political openness", "Apertura política",
                                    "Ouverture politique", "Politische Offenheit",
                                    "Abertura política"),
    "settings.recommendationStrength": ("Recommendation strength", "Fuerza de recomendación",
                                        "Force des recommandations", "Empfehlungsstärke",
                                        "Força das recomendações"),
    "settings.readingGoal": ("Daily reading goal", "Meta de lectura diaria",
                             "Objectif de lecture quotidien", "Tägliches Leseziel",
                             "Meta de leitura diária"),
    "settings.readingGoalDesc": ("Tracks today's progress on your dashboard.",
                                 "Registra el progreso de hoy en tu panel.",
                                 "Suit votre progression du jour sur le tableau de bord.",
                                 "Verfolgt den heutigen Fortschritt in Ihrer Übersicht.",
                                 "Acompanha o progresso de hoje no seu painel."),
    "settings.minutes": ("min", "min", "min", "Min.", "min"),
    "settings.unsaved": ("Unsaved changes", "Cambios sin guardar", "Modifications non enregistrées",
                         "Nicht gespeicherte Änderungen", "Alterações não salvas"),
    "settings.saveChanges": ("Save changes", "Guardar cambios", "Enregistrer", "Änderungen speichern",
                             "Salvar alterações"),
    "settings.savedShort": ("Saved", "Guardado", "Enregistré", "Gespeichert", "Salvo"),
    "common.reset": ("Reset", "Restablecer", "Réinitialiser", "Zurücksetzen", "Redefinir"),
    # -------- report --------
    "report.title": ("Health Report", "Informe de salud", "Rapport de santé",
                     "Gesundheitsbericht", "Relatório de saúde"),
    "report.overall": ("Overall", "General", "Global", "Gesamt", "Geral"),
    "report.metrics": ("Metrics", "Métricas", "Indicateurs", "Kennzahlen", "Métricas"),
    "report.updated": ("Updated", "Actualizado", "Mis à jour", "Aktualisiert", "Atualizado"),
    "report.viewpoint": ("Viewpoint balance", "Equilibrio de puntos de vista",
                         "Équilibre des points de vue", "Perspektivenbalance",
                         "Equilíbrio de perspectivas"),
    # band labels (3-value enum shown on report/dashboard) — client-side lookup, no calc change
    "band.Healthy": ("Healthy", "Saludable", "Sain", "Gesund", "Saudável"),
    "band.Fair": ("Fair", "Aceptable", "Correct", "Ausreichend", "Razoável"),
    "band.Needs work": ("Needs work", "Necesita mejorar", "À améliorer", "Verbesserungswürdig",
                        "Precisa melhorar"),
    "band.Unknown": ("Unknown", "Desconocido", "Inconnu", "Unbekannt", "Desconhecido"),
    # -------- metric copy (label / short / tooltip / description) --------
    "metric.topicDiversity.label": ("Topic Diversity", "Diversidad de temas",
                                    "Diversité des sujets", "Themenvielfalt",
                                    "Diversidade de temas"),
    "metric.topicDiversity.short": ("Topics", "Temas", "Sujets", "Themen", "Temas"),
    "metric.topicDiversity.tooltip": ("How many different subjects you read across.",
                                      "Cuántos temas distintos lees.",
                                      "Combien de sujets différents vous lisez.",
                                      "Über wie viele verschiedene Themen Sie lesen.",
                                      "Quantos assuntos diferentes você lê."),
    "metric.topicDiversity.description": (
        "How evenly your reading spreads across subjects. A high score means a broad diet; a low "
        "score means you circle the same few topics.",
        "Qué tan uniformemente se reparte tu lectura entre temas. Una puntuación alta indica una "
        "dieta amplia; una baja, que giras en torno a los mismos temas.",
        "À quel point votre lecture se répartit entre les sujets. Un score élevé indique un régime "
        "large ; un score faible, que vous tournez autour des mêmes sujets.",
        "Wie gleichmäßig sich Ihre Lektüre über Themen verteilt. Ein hoher Wert steht für eine "
        "breite Diät; ein niedriger dafür, dass Sie um dieselben Themen kreisen.",
        "Quão uniformemente sua leitura se distribui entre assuntos. Uma pontuação alta indica uma "
        "dieta ampla; uma baixa, que você gira em torno dos mesmos temas."),
    "metric.sourceDiversity.label": ("Source Diversity", "Diversidad de fuentes",
                                     "Diversité des sources", "Quellenvielfalt",
                                     "Diversidade de fontes"),
    "metric.sourceDiversity.short": ("Sources", "Fuentes", "Sources", "Quellen", "Fontes"),
    "metric.sourceDiversity.tooltip": ("How many distinct publishers you read.",
                                       "Cuántos medios distintos lees.",
                                       "Combien d'éditeurs distincts vous lisez.",
                                       "Wie viele verschiedene Verlage Sie lesen.",
                                       "Quantos veículos distintos você lê."),
    "metric.sourceDiversity.description": (
        "The effective number of publishers you rely on. Reading widely across outlets reduces the "
        "influence of any single newsroom's framing.",
        "El número efectivo de medios en los que confías. Leer en muchos medios reduce la "
        "influencia del enfoque de una sola redacción.",
        "Le nombre effectif d'éditeurs sur lesquels vous vous appuyez. Lire largement réduit "
        "l'influence du cadrage d'une seule rédaction.",
        "Die effektive Zahl der Verlage, auf die Sie sich stützen. Breites Lesen verringert den "
        "Einfluss der Sichtweise einer einzelnen Redaktion.",
        "O número efetivo de veículos em que você confia. Ler amplamente reduz a influência do "
        "enquadramento de uma única redação."),
    "metric.reportingRatio.label": ("Reporting Ratio", "Proporción informativa",
                                    "Ratio d'information", "Berichtsanteil",
                                    "Proporção de reportagem"),
    "metric.reportingRatio.short": ("Reporting", "Información", "Info", "Bericht", "Reportagem"),
    "metric.reportingRatio.tooltip": ("How much of your reading is reporting vs. opinion.",
                                      "Cuánta de tu lectura es información frente a opinión.",
                                      "Quelle part de votre lecture est de l'information vs. de l'opinion.",
                                      "Wie viel Ihrer Lektüre Bericht statt Meinung ist.",
                                      "Quanto da sua leitura é reportagem vs. opinião."),
    "metric.reportingRatio.description": (
        "The share of your reading that is factual reporting rather than opinion or commentary. "
        "Balance matters — both have a place.",
        "La parte de tu lectura que es información factual y no opinión o comentario. El equilibrio "
        "importa: ambas tienen su lugar.",
        "La part de votre lecture qui relève de l'information factuelle plutôt que de l'opinion. "
        "L'équilibre compte — les deux ont leur place.",
        "Der Anteil Ihrer Lektüre, der sachlicher Bericht statt Meinung ist. Balance zählt — "
        "beides hat seinen Platz.",
        "A parcela da sua leitura que é reportagem factual em vez de opinião. O equilíbrio importa "
        "— ambos têm seu lugar."),
    "metric.emotionalBalance.label": ("Emotional Balance", "Equilibrio emocional",
                                      "Équilibre émotionnel", "Emotionale Balance",
                                      "Equilíbrio emocional"),
    "metric.emotionalBalance.short": ("Tone", "Tono", "Ton", "Ton", "Tom"),
    "metric.emotionalBalance.tooltip": ("How calm vs. charged the tone of your reading is.",
                                        "Qué tan calmado o cargado es el tono de tu lectura.",
                                        "À quel point le ton de vos lectures est calme ou chargé.",
                                        "Wie ruhig oder aufgeladen der Ton Ihrer Lektüre ist.",
                                        "Quão calmo ou carregado é o tom da sua leitura."),
    "metric.emotionalBalance.description": (
        "How much of your reading leans on fear and outrage versus calm analysis. A charged diet "
        "can distort perception over time.",
        "Cuánto se apoya tu lectura en el miedo y la indignación frente al análisis sereno. Una "
        "dieta cargada puede distorsionar la percepción con el tiempo.",
        "À quel point vos lectures s'appuient sur la peur et l'indignation plutôt que sur l'analyse "
        "posée. Un régime chargé peut fausser la perception avec le temps.",
        "Wie sehr Ihre Lektüre auf Angst und Empörung statt auf ruhige Analyse setzt. Eine "
        "aufgeladene Diät kann die Wahrnehmung mit der Zeit verzerren.",
        "Quanto sua leitura se apoia em medo e indignação em vez de análise serena. Uma dieta "
        "carregada pode distorcer a percepção com o tempo."),
    "metric.echoChamber.label": ("Echo Chamber Score", "Puntuación de cámara de eco",
                                 "Score de chambre d'écho", "Echokammer-Wert",
                                 "Pontuação de câmara de eco"),
    "metric.echoChamber.short": ("Echo", "Eco", "Écho", "Echo", "Eco"),
    "metric.echoChamber.tooltip": ("How one-sided your political reading is.",
                                   "Qué tan unilateral es tu lectura política.",
                                   "À quel point votre lecture politique est unilatérale.",
                                   "Wie einseitig Ihre politische Lektüre ist.",
                                   "Quão unilateral é sua leitura política."),
    "metric.echoChamber.description": (
        "How balanced your left/right reading is. A higher score means you're less one-sided — you "
        "hear more than one side of contested topics.",
        "Qué tan equilibrada es tu lectura izquierda/derecha. Una puntuación más alta indica menos "
        "unilateralidad: escuchas más de un lado de los temas en disputa.",
        "À quel point votre lecture gauche/droite est équilibrée. Un score plus élevé signifie "
        "moins d'unilatéralité — vous entendez plus d'un camp sur les sujets débattus.",
        "Wie ausgewogen Ihre Links/Rechts-Lektüre ist. Ein höherer Wert bedeutet weniger "
        "Einseitigkeit — Sie hören mehr als eine Seite strittiger Themen.",
        "Quão equilibrada é sua leitura esquerda/direita. Uma pontuação mais alta significa menos "
        "unilateralidade — você ouve mais de um lado dos temas em disputa."),
    "metric.viewpointBalance.label": ("Viewpoint Balance", "Equilibrio de puntos de vista",
                                      "Équilibre des points de vue", "Perspektivenbalance",
                                      "Equilíbrio de perspectivas"),
    "metric.viewpointBalance.short": ("Viewpoints", "Puntos de vista", "Points de vue",
                                      "Perspektiven", "Perspectivas"),
    "metric.viewpointBalance.tooltip": ("How evenly you read across the political spectrum.",
                                        "Qué tan uniformemente lees en el espectro político.",
                                        "À quel point vous lisez sur tout l'échiquier politique.",
                                        "Wie gleichmäßig Sie über das politische Spektrum lesen.",
                                        "Quão uniformemente você lê no espectro político."),
    "metric.viewpointBalance.description": (
        "How evenly your reading is spread across left, center, and right. Balance here means you "
        "encounter the full range of the debate.",
        "Qué tan uniformemente se reparte tu lectura entre izquierda, centro y derecha. El "
        "equilibrio aquí implica que encuentras todo el rango del debate.",
        "À quel point votre lecture se répartit entre gauche, centre et droite. L'équilibre "
        "signifie que vous rencontrez tout l'éventail du débat.",
        "Wie gleichmäßig sich Ihre Lektüre über links, Mitte und rechts verteilt. Balance bedeutet "
        "hier, dass Sie die ganze Bandbreite der Debatte antreffen.",
        "Quão uniformemente sua leitura se distribui entre esquerda, centro e direita. O equilíbrio "
        "aqui significa que você encontra toda a amplitude do debate."),
    "metric.openMindedness.label": ("Open-Mindedness", "Apertura mental", "Ouverture d'esprit",
                                    "Aufgeschlossenheit", "Mente aberta"),
    "metric.openMindedness.short": ("Openness", "Apertura", "Ouverture", "Offenheit", "Abertura"),
    "metric.openMindedness.tooltip": ("How often you open cross-cutting recommendations.",
                                      "Con qué frecuencia abres recomendaciones transversales.",
                                      "À quelle fréquence vous ouvrez des recommandations transversales.",
                                      "Wie oft Sie überbrückende Empfehlungen öffnen.",
                                      "Com que frequência você abre recomendações transversais."),
    "metric.openMindedness.description": (
        "How often you actually open recommendations that cross your usual viewpoint. It measures "
        "engagement with the other side, not just exposure to it.",
        "Con qué frecuencia abres realmente recomendaciones que cruzan tu punto de vista habitual. "
        "Mide el compromiso con el otro lado, no solo la exposición.",
        "À quelle fréquence vous ouvrez réellement des recommandations qui dépassent votre point de "
        "vue habituel. Cela mesure l'engagement avec l'autre camp, pas seulement l'exposition.",
        "Wie oft Sie tatsächlich Empfehlungen öffnen, die Ihre gewohnte Sichtweise überschreiten. "
        "Es misst die Auseinandersetzung mit der Gegenseite, nicht nur die Exposition.",
        "Com que frequência você realmente abre recomendações que cruzam seu ponto de vista "
        "habitual. Mede o engajamento com o outro lado, não apenas a exposição."),
    "metric.confidence.label": ("Confidence", "Confianza", "Confiance", "Konfidenz", "Confiança"),
    "metric.confidence.short": ("Confidence", "Confianza", "Confiance", "Konfidenz", "Confiança"),
    "metric.confidence.tooltip": ("How sure the model is about your lean estimates.",
                                  "Qué tan seguro está el modelo de tus estimaciones de sesgo.",
                                  "À quel point le modèle est sûr de vos estimations d'orientation.",
                                  "Wie sicher das Modell bei Ihren Tendenz-Schätzungen ist.",
                                  "Quão confiante o modelo está nas estimativas de viés."),
    "metric.confidence.description": (
        "How confident the model is in the political-lean estimates behind your report. Lower "
        "confidence means the scores rest on fewer clearly-placed articles.",
        "Qué tan seguro está el modelo en las estimaciones de sesgo político tras tu informe. Menor "
        "confianza significa que las puntuaciones se apoyan en menos artículos claramente ubicados.",
        "À quel point le modèle est confiant dans les estimations d'orientation politique de votre "
        "rapport. Une confiance moindre signifie que les scores reposent sur moins d'articles "
        "clairement situés.",
        "Wie zuversichtlich das Modell bei den politischen Tendenz-Schätzungen Ihres Berichts ist. "
        "Geringere Konfidenz bedeutet, dass die Werte auf weniger klar verorteten Artikeln beruhen.",
        "Quão confiante o modelo está nas estimativas de viés político do seu relatório. Menor "
        "confiança significa que as pontuações se apoiam em menos artigos claramente posicionados."),
    # -------- recommendation card --------
    "rec.title": ("Recommendations", "Recomendaciones", "Recommandations", "Empfehlungen",
                  "Recomendações"),
    "rec.subtitle": ("Reads picked to balance your diet — each shows exactly why it's here.",
                     "Lecturas para equilibrar tu dieta: cada una muestra por qué está aquí.",
                     "Des lectures pour équilibrer votre régime — chacune montre pourquoi elle est là.",
                     "Lektüre zum Ausgleich Ihrer Diät — jede zeigt genau, warum sie hier ist.",
                     "Leituras para equilibrar sua dieta — cada uma mostra exatamente por que está aqui."),
    "rec.whyThisArticle": ("Why this article?", "¿Por qué este artículo?", "Pourquoi cet article ?",
                           "Warum dieser Artikel?", "Por que este artigo?"),
    "rec.helps": ("Helps {metric}", "Mejora {metric}", "Améliore {metric}", "Verbessert {metric}",
                  "Melhora {metric}"),
    "rec.strategy.rwe-b": ("Bridging", "Puente", "Passerelle", "Brücke", "Ponte"),
    "rec.strategy.rwe-d": ("Discovery", "Descubrimiento", "Découverte", "Entdeckung", "Descoberta"),
    "rec.strategy.adaptive": ("For you", "Para ti", "Pour vous", "Für Sie", "Para você"),
    "rec.readLater": ("Read later", "Leer después", "Lire plus tard", "Später lesen", "Ler depois"),
    "rec.like": ("Like", "Me gusta", "J'aime", "Gefällt mir", "Curtir"),
    "rec.dislike": ("Dislike", "No me gusta", "Je n'aime pas", "Gefällt mir nicht", "Não curtir"),
    "rec.why": ("Why?", "¿Por qué?", "Pourquoi ?", "Warum?", "Por quê?"),
    # -------- explanation templates (localized from the resolver's structured type) --------
    "explanation.story_match.same_event": (
        "You already read this story from {readPublisher}. Here's how {recPublisher} covered the same story.",
        "Ya leíste esta noticia en {readPublisher}. Así la cubrió {recPublisher}.",
        "Vous avez déjà lu ce sujet sur {readPublisher}. Voici comment {recPublisher} l'a couvert.",
        "Sie haben dieses Thema bereits bei {readPublisher} gelesen. So hat {recPublisher} darüber berichtet.",
        "Você já leu esta história em {readPublisher}. Veja como {recPublisher} cobriu o mesmo assunto."),
    "explanation.story_match.follow_up": (
        "You already read the earlier coverage from {readPublisher}. Here's {recPublisher}'s latest update.",
        "Ya leíste la cobertura anterior de {readPublisher}. Aquí está la última actualización de {recPublisher}.",
        "Vous avez déjà lu la couverture précédente de {readPublisher}. Voici la dernière mise à jour de {recPublisher}.",
        "Sie haben die frühere Berichterstattung von {readPublisher} bereits gelesen. Hier ist das neueste Update von {recPublisher}.",
        "Você já leu a cobertura anterior de {readPublisher}. Aqui está a atualização mais recente de {recPublisher}."),
    "explanation.story_match.following": (
        "You've been following this story. Here's {recPublisher}'s coverage.",
        "Has estado siguiendo esta noticia. Aquí está la cobertura de {recPublisher}.",
        "Vous suivez ce sujet. Voici la couverture de {recPublisher}.",
        "Sie verfolgen dieses Thema. Hier ist die Berichterstattung von {recPublisher}.",
        "Você está acompanhando esta história. Aqui está a cobertura de {recPublisher}."),
    "explanation.topic_continuity.perspective": (
        "You've been reading about {topic}. Here's another perspective.",
        "Has estado leyendo sobre {topic}. Aquí tienes otra perspectiva.",
        "Vous lisez sur {topic}. Voici un autre point de vue.",
        "Sie lesen über {topic}. Hier ist eine andere Perspektive.",
        "Você tem lido sobre {topic}. Aqui está outra perspectiva."),
    "explanation.topic_continuity.outlet": (
        "You've been reading about {topic}. Here's more coverage from another outlet.",
        "Has estado leyendo sobre {topic}. Aquí hay más cobertura de otro medio.",
        "Vous lisez sur {topic}. Voici plus de couverture d'un autre média.",
        "Sie lesen über {topic}. Hier ist mehr Berichterstattung eines anderen Mediums.",
        "Você tem lido sobre {topic}. Aqui há mais cobertura de outro veículo."),
    "explanation.new_publisher.never": (
        "You've never read {publisher} before. This broadens your source diversity.",
        "Nunca has leído {publisher}. Esto amplía tu diversidad de fuentes.",
        "Vous n'avez jamais lu {publisher}. Cela élargit votre diversité de sources.",
        "Sie haben {publisher} noch nie gelesen. Das erweitert Ihre Quellenvielfalt.",
        "Você nunca leu {publisher}. Isso amplia sua diversidade de fontes."),
    "explanation.new_publisher.rarely": (
        "You rarely read {publisher}. This broadens your source diversity.",
        "Rara vez lees {publisher}. Esto amplía tu diversidad de fuentes.",
        "Vous lisez rarement {publisher}. Cela élargit votre diversité de sources.",
        "Sie lesen {publisher} selten. Das erweitert Ihre Quellenvielfalt.",
        "Você raramente lê {publisher}. Isso amplia sua diversidade de fontes."),
    "explanation.bridge": (
        "This article offers another political perspective.",
        "Este artículo ofrece otra perspectiva política.",
        "Cet article offre un autre point de vue politique.",
        "Dieser Artikel bietet eine andere politische Perspektive.",
        "Este artigo oferece outra perspectiva política."),
    "explanation.long_tail": (
        "This article introduces a less frequently recommended source.",
        "Este artículo presenta una fuente menos recomendada.",
        "Cet article présente une source moins souvent recommandée.",
        "Dieser Artikel führt eine seltener empfohlene Quelle ein.",
        "Este artigo apresenta uma fonte menos recomendada."),
    "explanation.coverage_breadth.topic": (
        "Broadens your {topic} coverage beyond your usual mix.",
        "Amplía tu cobertura de {topic} más allá de tu mezcla habitual.",
        "Élargit votre couverture de {topic} au-delà de votre mélange habituel.",
        "Erweitert Ihre {topic}-Abdeckung über Ihre übliche Mischung hinaus.",
        "Amplia sua cobertura de {topic} além da sua mistura habitual."),
    "explanation.coverage_breadth.generic": (
        "Broadens your coverage beyond your usual mix.",
        "Amplía tu cobertura más allá de tu mezcla habitual.",
        "Élargit votre couverture au-delà de votre mélange habituel.",
        "Erweitert Ihre Abdeckung über Ihre übliche Mischung hinaus.",
        "Amplia sua cobertura além da sua mistura habitual."),
    # -------- empty / error states --------
    "states.empty.title": ("Nothing here yet", "Aún no hay nada", "Rien pour l'instant",
                           "Noch nichts hier", "Nada aqui ainda"),
    "states.error.title": ("Something went wrong", "Algo salió mal", "Une erreur est survenue",
                           "Etwas ist schiefgelaufen", "Algo deu errado"),
    "states.error.body": ("We couldn't load this. Please try again.",
                          "No pudimos cargar esto. Inténtalo de nuevo.",
                          "Impossible de charger ceci. Veuillez réessayer.",
                          "Wir konnten dies nicht laden. Bitte erneut versuchen.",
                          "Não foi possível carregar isto. Tente novamente."),
    "states.offline.title": ("Engine unavailable", "Motor no disponible", "Moteur indisponible",
                             "Engine nicht verfügbar", "Motor indisponível"),
    # -------- relative time (words for timeAgo; the date fallback is locale-formatted) --------
    "time.justNow": ("just now", "ahora mismo", "à l'instant", "gerade eben", "agora mesmo"),
    "time.minutesAgo": ("{n}m ago", "hace {n} min", "il y a {n} min", "vor {n} Min.", "{n} min atrás"),
    "time.hoursAgo": ("{n}h ago", "hace {n} h", "il y a {n} h", "vor {n} Std.", "{n} h atrás"),
    "time.daysAgo": ("{n}d ago", "hace {n} d", "il y a {n} j", "vor {n} T.", "{n} d atrás"),
}

LANGS = ("en", "es", "fr", "de", "pt")
for i, lang in enumerate(LANGS):
    catalog = {k: v[i] for k, v in M.items()}
    (OUT / f"{lang}.json").write_text(json.dumps(catalog, ensure_ascii=False, indent=1) + "\n")
    print(f"wrote {lang}.json  ({len(catalog)} keys)")

# parity self-check
sizes = {lang: len(json.loads((OUT / f"{lang}.json").read_text())) for lang in LANGS}
assert len(set(sizes.values())) == 1, f"key-count mismatch: {sizes}"
print("all catalogs share", next(iter(sizes.values())), "keys")
