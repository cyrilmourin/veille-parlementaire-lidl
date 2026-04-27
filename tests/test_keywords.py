"""Tests unitaires du matcher de mots-clés.

2026-04-27 : tests transposés du fork sport vers le lexique Lidl
(grande distribution alimentaire). Les anciens noms (`dispositif`,
`evenement`, `federation`) référençaient des familles sport qui
n'existent pas dans le lexique Lidl ; ils sont renommés vers les
familles Lidl correspondantes (`theme_negociations`, `theme_outremer`,
`acteur`).
"""
from pathlib import Path

import pytest

from src.keywords import KeywordMatcher, _normalize


CONFIG = Path(__file__).resolve().parent.parent / "config" / "keywords.yml"


@pytest.fixture(scope="module")
def m():
    return KeywordMatcher(CONFIG)


def test_normalize_accents():
    assert _normalize("Éducation physique et sportive") == "education physique et sportive"
    # Ponctuation interne préservée (mots avec tiret).
    assert _normalize("  Hard-Discount  ") == "hard-discount"


def test_match_theme_negociations(m):
    """Un texte mentionnant Loi Descrozaille matche le keyword direct
    + la famille theme_negociations."""
    kws, fams = m.match("Bilan d'application de la Loi Descrozaille deux ans après")
    assert "Loi Descrozaille" in kws
    assert "theme_negociations" in fams


def test_match_acteur(m):
    """Lidl est un keyword direct de la famille acteur."""
    kws, fams = m.match("Audition du PDG de Lidl France à l'Assemblée")
    assert "Lidl" in kws
    assert "acteur" in fams


def test_match_multiple_acteurs(m):
    """Plusieurs enseignes simultanées sont toutes capturées."""
    kws, _ = m.match("Réforme du circuit Lidl et Auchan dans la grande distribution")
    assert "Lidl" in kws and "Auchan" in kws


def test_match_theme_outremer(m):
    """BQP est un keyword direct hyper-spécifique de la famille
    theme_outremer (bouclier qualité prix)."""
    kws, fams = m.match("Mise à jour du dispositif BQP outre-mer")
    assert "BQP" in kws
    assert "theme_outremer" in fams


def test_no_match_unrelated(m):
    kws, _ = m.match("Plan national biodiversité 2026")
    assert kws == []


def test_recapitalize_maps_legacy_lowercase_kws_to_yaml_form(m):
    """Les items pré-R13-B ont des kws stockés en minuscules non-accentuées.
    `recapitalize` les remappe sur la forme du yaml courant (capitalisée)."""
    out = m.recapitalize(["lidl", "egalim", "srp+10", "auchan"])
    # Chaque élément retrouve sa forme canonique (capitalisée ou sigle).
    assert "Lidl" in out
    assert "EGalim" in out
    assert "SRP+10" in out
    assert "Auchan" in out
    # Aucun doublon même si plusieurs variantes de casse sont passées.
    assert len(out) == len(set(out))


def test_recapitalize_preserves_order_and_dedupes(m):
    out = m.recapitalize(["Lidl", "lidl", "LIDL"])
    assert out == ["Lidl"]


def test_recapitalize_leaves_unknown_kws_untouched(m):
    """Un kw absent du yaml (ex. source externe, ancien yaml) reste tel quel."""
    out = m.recapitalize(["Mot-inconnu-XYZ", "Lidl"])
    assert "Mot-inconnu-XYZ" in out
    assert "Lidl" in out


def test_recapitalize_empty_input(m):
    assert m.recapitalize([]) == []
    assert m.recapitalize(None) == []
