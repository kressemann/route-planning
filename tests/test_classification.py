from routeplanning.article_rules import (
    EINZEL_1_9,
    KOMPLETTE_VE,
    KRESSE,
    SCHNITTWARE,
    UNKLASSIFIZIERT,
    classify_article,
)


def test_override_shira_mix():
    result = classify_article("Shira Mix")
    assert result.article_class == KOMPLETTE_VE
    assert result.reason == "override"


def test_override_sakina_mix():
    result = classify_article("Sakina Mix")
    assert result.article_class == KOMPLETTE_VE


def test_override_gartenkresse_pad():
    result = classify_article("Gartenkresse 1/12 Schale/Pad")
    assert result.article_class == KRESSE
    assert result.reason == "override"


def test_override_oxalis_mix():
    result = classify_article("Oxalis Mix 15 Stk.")
    assert result.article_class == SCHNITTWARE
    assert result.reason == "override"


def test_override_steige_keyword():
    result = classify_article("Afiora Steige")
    assert result.article_class == KOMPLETTE_VE
    assert result.reason == "override"


def test_override_steige_keyword_case_insensitive_substring():
    result = classify_article("Bio-Kraeuter steige gross")
    assert result.article_class == KOMPLETTE_VE


def test_override_beats_produktart_custom_field():
    # Override (a) hat Vorrang vor Produktart (b), selbst wenn beide zutreffen.
    result = classify_article("Shira Mix", produktart="Schnittware")
    assert result.article_class == KOMPLETTE_VE


def test_produktart_custom_field_schnittware():
    result = classify_article("Irgendein Artikel ohne Muster", produktart="Schnittware")
    assert result.article_class == SCHNITTWARE
    assert result.reason == "produktart"


def test_produktart_other_value_ignored():
    result = classify_article("Irgendein Artikel", produktart="Frischware")
    assert result.article_class == UNKLASSIFIZIERT


def test_generic_gartenkresse_keyword():
    result = classify_article("Gartenkresse Schale gross")
    assert result.article_class == KRESSE
    assert result.reason == "kresse"


def test_1_9_pattern():
    result = classify_article("Radieschen Mix 1/9")
    assert result.article_class == EINZEL_1_9
    assert result.reason == "1/9"


def test_gramm_pattern():
    result = classify_article("Roter Rettich Microgreens 200g")
    assert result.article_class == SCHNITTWARE
    assert result.reason == "gramm"


def test_stk_pattern():
    result = classify_article("Pflücksalat Stk.")
    assert result.article_class == SCHNITTWARE
    assert result.reason == "stk"


def test_unklassifiziert_fallback():
    result = classify_article("Voellig unbekannter Artikelname")
    assert result.article_class == UNKLASSIFIZIERT
    assert result.reason == "keine_regel_getroffen"


def test_priority_gramm_before_stk_is_irrelevant_when_both_absent():
    # Kein Grammmuster, kein Stk. -> unklassifiziert, nicht faelschlich SCHNITTWARE.
    result = classify_article("Sonderposten X-42")
    assert result.article_class == UNKLASSIFIZIERT


# -- Generische "Mix"-Regel (g) -----------------------------------------


def test_generic_mix_chefs_mix():
    result = classify_article("Chefs-Mix")
    assert result.article_class == KOMPLETTE_VE
    assert result.reason == "mix"


def test_generic_mix_any_other_article_name():
    result = classify_article("Bunter Salat Mix")
    assert result.article_class == KOMPLETTE_VE
    assert result.reason == "mix"


def test_generic_mix_case_insensitive():
    assert classify_article("CHEFS-MIX").article_class == KOMPLETTE_VE
    assert classify_article("chefs-mix").article_class == KOMPLETTE_VE
    assert classify_article("Chefs-MiX").article_class == KOMPLETTE_VE


def test_mix_article_with_produktart_schnittware_stays_schnittware():
    # Schnittware (b) hat Vorrang vor der generischen Mix-Regel (g).
    result = classify_article("Irgendein Mix", produktart="Schnittware")
    assert result.article_class == SCHNITTWARE
    assert result.reason == "produktart"


def test_mix_article_with_gramm_pattern_stays_schnittware():
    # Schnittware (e) hat Vorrang vor der generischen Mix-Regel (g).
    result = classify_article("Kraeuter Mix 200g")
    assert result.article_class == SCHNITTWARE
    assert result.reason == "gramm"


def test_mix_article_with_stk_pattern_stays_schnittware():
    # Schnittware (f) hat Vorrang vor der generischen Mix-Regel (g).
    result = classify_article("Salat Mix Stk.")
    assert result.article_class == SCHNITTWARE
    assert result.reason == "stk"


def test_mix_article_matching_1_9_pattern_stays_einzel_1_9():
    # 1/9 (d) hat Vorrang vor der generischen Mix-Regel (g) - bestehendes
    # Verhalten (siehe test_1_9_pattern) darf sich durch die neue Regel
    # nicht aendern.
    result = classify_article("Radieschen Mix 1/9")
    assert result.article_class == EINZEL_1_9
    assert result.reason == "1/9"


def test_mix_override_still_beats_generic_mix_rule():
    # Bestehende spezifische Overrides (a) fuer "Shira Mix"/"Sakina Mix"
    # bleiben unveraendert - sie werden schon vorher getroffen.
    result = classify_article("Shira Mix")
    assert result.article_class == KOMPLETTE_VE
    assert result.reason == "override"
