"""French / English interface.

Everything the agent SAYS goes through `t(lang, key, ...)`; everything it
NAMES (pages, fields, visit columns, statuses, notes) goes through the
helpers below.  The data itself is never translated: values are stored and
shown exactly as written on paper, and the printed options of the form stay
in French in both languages because that is what the midwife sees on paper.

English is the default so that logs, tests and the audit trail read the same
as before; a midwife switches with the language button, or by typing
*français* / *english* in the chat.  The choice is stored per account.
"""
from __future__ import annotations

import os
import re

from . import schema as S

LANGS = ("en", "fr")
DEFAULT_LANG = os.environ.get("DAYONE_LANG", "en") if os.environ.get("DAYONE_LANG", "en") in LANGS else "en"
# What a midwife may type to switch language (also sent by the language button).
LANG_COMMANDS = {"english": "en", "lang:en": "en", "français": "fr", "francais": "fr", "lang:fr": "fr"}

# key: (English, French).  English wording is unchanged from the single-language version.
M: dict[str, tuple[str, str]] = {
    # ---- general ---------------------------------------------------------
    "help": ("Send me a photo of a registry page to start. Send all pages of the same registry, then tap *Done*.\n"
             "Commands: *status* (queue and records) · *history* (patients and their visits) · *help*",
             "Envoyez-moi une photo d'une page du registre pour commencer. Envoyez toutes les pages du même "
             "registre, puis touchez *Terminé*.\n"
             "Commandes : *statut* (file d'attente et registres) · *historique* (patientes et leurs visites) · *aide*"),
    "lang_set": ("Language: English. Type *français* to switch to French.",
                 "Langue : français. Tapez *english* pour passer à l'anglais."),
    "button": ("(button)", "(bouton)"),
    "not_found": ("I cannot find that registry.", "Je ne trouve pas ce registre."),
    "nothing_to_do": ("Registry {tag} is {state}. Nothing to do.", "Le registre {tag} est {state}. Rien à faire."),
    "registry_is": ("This registry is {state}.", "Ce registre est {state}."),
    # ---- capture ---------------------------------------------------------
    "new_registry": ("I saved this photo as a NEW registry. The review you were doing is kept; "
                     "type *status* to come back to it.",
                     "J'ai enregistré cette photo comme un NOUVEAU registre. La vérification en cours est conservée ; "
                     "tapez *statut* pour y revenir."),
    "dup_photo": ("This is exactly the same photo as one already saved for this registry, so I kept only one copy.",
                  "Cette photo est exactement la même qu'une photo déjà enregistrée pour ce registre ; "
                  "je n'en ai gardé qu'une copie."),
    "page_saved": ("📄 Page {n} saved on this phone (encrypted). Send the next page of the SAME registry, or tap Done.",
                   "📄 Page {n} enregistrée sur ce téléphone (chiffrée). Envoyez la page suivante du MÊME registre, "
                   "ou touchez Terminé."),
    "btn_done_short": ("Done", "Terminé"),
    "btn_done": ("Done - no more pages", "Terminé - plus de pages"),
    "send_next_or_done": ("Send the next page as a photo, or tap Done.",
                          "Envoyez la page suivante en photo, ou touchez Terminé."),
    "send_next_page": ("Send the next page, or tap Done.", "Envoyez la page suivante, ou touchez Terminé."),
    "queued_reading": ("⏳ Registry {tag}: {n} page(s) queued. I am reading them now.",
                       "⏳ Registre {tag} : {n} page(s) en file d'attente. Je les lis maintenant."),
    "pending": ("📴 Registry {tag}: {n} page(s) saved and waiting - *Pending AI processing* ({why}). Nothing is lost. "
                "I will read them as soon as I can, or you can enter the data yourself now.",
                "📴 Registre {tag} : {n} page(s) enregistrée(s) et en attente - *En attente de traitement par l'IA* "
                "({why}). Rien n'est perdu. Je les lirai dès que possible, ou vous pouvez saisir les données "
                "vous-même maintenant."),
    "btn_manual_now": ("Enter manually now", "Saisir manuellement maintenant"),
    "still_pending": ("This registry is still *Pending AI processing*.",
                      "Ce registre est toujours *En attente de traitement par l'IA*."),
    # ---- image-quality check (on the device, before the page goes to the AI) ---
    "quality_warn": ("⚠️ Page {n} is saved, but the photo may be hard to read: {reasons}. "
                     "Do you want to take it again?",
                     "⚠️ La page {n} est enregistrée, mais la photo risque d'être difficile à lire : {reasons}. "
                     "Voulez-vous la reprendre ?"),
    "btn_quality_retake": ("📷 Retake this photo", "📷 Reprendre cette photo"),
    "btn_quality_keep": ("Keep it anyway", "La garder quand même"),
    "quality_retake": ("📷 Send the new photo of this page. The first one is kept until the new one is saved.",
                       "📷 Envoyez la nouvelle photo de cette page. La première est conservée jusqu'à "
                       "l'enregistrement de la nouvelle."),
    "quality_kept": ("OK, I keep this photo. Send the next page of the SAME registry, or tap Done.",
                     "D'accord, je garde cette photo. Envoyez la page suivante du MÊME registre, ou touchez Terminé."),
    "quality_replaced": ("📄 Page {n}: the new photo replaces the first one. "
                         "Send the next page of the SAME registry, or tap Done.",
                         "📄 Page {n} : la nouvelle photo remplace la première. "
                         "Envoyez la page suivante du MÊME registre, ou touchez Terminé."),
    "quality_note": ("⚠️ Note: this new photo may be hard to read too: {reasons}.",
                     "⚠️ Remarque : cette nouvelle photo risque aussi d'être difficile à lire : {reasons}."),
    "quality.blurred": ("it looks blurred", "elle semble floue"),
    "quality.dark": ("it is too dark", "elle est trop sombre"),
    "quality.low_contrast": ("the writing barely stands out from the paper",
                             "l'écriture ressort à peine du papier"),
    "quality.small": ("the image is very small", "l'image est très petite"),
    # ---- worker notifications -----------------------------------------------
    "read_ready": ("Registry {tag} has been read and is ready for your review.",
                   "Le registre {tag} a été lu et attend votre vérification."),
    "btn_review_now": ("Review it now", "Vérifier maintenant"),
    "failed": ("⚠️ I could not read registry {tag} after several tries ({reason}). "
               "The photos are safe. What do you want to do?",
               "⚠️ Je n'ai pas pu lire le registre {tag} après plusieurs essais ({reason}). "
               "Les photos sont en sécurité. Que voulez-vous faire ?"),
    "btn_manual": ("Enter manually", "Saisir manuellement"),
    "btn_retry_ai": ("Let the AI try again", "Laisser l'IA réessayer"),
    "synced": ("☁️ Registry {tag} is synchronised with the server.",
               "☁️ Le registre {tag} est synchronisé avec le serveur."),
    "sync_refused": ("⚠️ The server refused registry {tag} several times. It is still safely saved on this phone.",
                     "⚠️ Le serveur a refusé le registre {tag} plusieurs fois. "
                     "Il reste enregistré en sécurité sur ce téléphone."),
    "btn_sync_again": ("Try to sync again", "Réessayer la synchronisation"),
    "sync_retry": ("I will try to synchronise it again.", "Je vais réessayer de le synchroniser."),
    "ai_waiting": ("⏳ Registry {tag} is still *Pending AI processing*: {why}. Nothing is lost. I try again "
                   "automatically (next try in about {secs} s), or you can enter the data yourself now.{detail}",
                   "⏳ Le registre {tag} est toujours *En attente de traitement par l'IA* : {why}. Rien n'est perdu. "
                   "Je réessaie automatiquement (prochain essai dans environ {secs} s), ou vous pouvez saisir les "
                   "données vous-même maintenant.{detail}"),
    "ai_detail": ("\nDetail: {detail}", "\nDétail : {detail}"),
    "btn_ai_now": ("Try again now", "Réessayer maintenant"),
    "ai_now": ("OK, I am asking the AI again now.", "D'accord, je redemande à l'IA maintenant."),
    # why the AI cannot be used (codes from extractors.base.WHY_CODES)
    "ai_why.offline": ("no connection", "pas de connexion"),
    "ai_why.switched_off": ("the AI is not available", "l'IA n'est pas disponible"),
    "ai_why.no_key": ("the AI is not available: no API key is set on this device",
                      "l'IA n'est pas disponible : aucune clé API n'est configurée sur cet appareil"),
    "ai_why.overloaded": ("the AI service is overloaded", "le service d'IA est surchargé"),
    "ai_why.rate_limited": ("the AI service quota or rate limit is reached",
                            "le quota ou la limite de débit du service d'IA est atteint"),
    "ai_why.key_refused": ("the AI service refuses this device's key - whoever set up the app must fix the key",
                           "le service d'IA refuse la clé de cet appareil - la personne qui a installé "
                           "l'application doit corriger la clé"),
    "ai_why.model_unknown": ("the AI service does not know the configured model - whoever set up the app must "
                             "fix the model name",
                             "le service d'IA ne connaît pas le modèle configuré - la personne qui a installé "
                             "l'application doit corriger le nom du modèle"),
    "ai_why.network": ("the AI service cannot be reached", "le service d'IA est injoignable"),
    # ---- review ------------------------------------------------------------
    "summary_head": ("📋 Registry {tag} - here is what I read:", "📋 Registre {tag} - voici ce que j'ai lu :"),
    "summary_line": ("• {sec}: {read} read, {check} to check, {blank} blank",
                     "• {sec} : {read} lu(s), {check} à vérifier, {blank} vide(s)"),
    "summary_other": (", {other} not applicable/unknown", ", {other} sans objet/inconnu(s)"),
    "summary_missing": ("• {n} page(s) not read by the AI.", "• {n} page(s) non lue(s) par l'IA."),
    "not_sure_intro": ("\nI am NOT sure about {n} field(s). I will ask you about each one "
                       "(I ask whenever my confidence is below {pct}{prov}).",
                       "\nJe ne suis PAS sûr de {n} champ(s). Je vais vous interroger sur chacun "
                       "(je demande dès que ma confiance est inférieure à {pct}{prov})."),
    "provisional": (", a provisional setting", ", réglage provisoire"),
    "not_sure_arabic": (" On pages with entries in Arabic script, I ask about every number below {pct}.",
                        " Sur les pages comportant des entrées en écriture arabe, je demande pour chaque nombre "
                        "en dessous de {pct}."),
    "not_sure_arabic_all": (" On pages with entries in Arabic script, I ask about every value below {pct}.",
                            " Sur les pages comportant des entrées en écriture arabe, je demande pour chaque "
                            "valeur en dessous de {pct}."),
    "menu_remaining": ("{n} field(s) still need your review before you can confirm.",
                       "{n} champ(s) doivent encore être vérifiés avant de pouvoir confirmer."),
    "menu_nothing": ("Nothing is waiting for review. Please check the summary and confirm, or change something.",
                     "Rien n'attend de vérification. Veuillez contrôler le résumé et confirmer, "
                     "ou modifier quelque chose."),
    "btn_review_remaining": ("Review remaining ({n})", "Vérifier le reste ({n})"),
    "btn_confirm": ("✅ Confirm", "✅ Confirmer"),
    "btn_edit": ("✏️ Edit a field", "✏️ Modifier un champ"),
    "btn_retake": ("📷 Retake a photo", "📷 Reprendre une photo"),
    "btn_show_all": ("📋 Show all fields", "📋 Afficher tous les champs"),
    "btn_ai_read": ("🤖 Let the AI read the photos", "🤖 Laisser l'IA lire les photos"),
    "cannot_confirm": ("I cannot confirm yet.", "Je ne peux pas encore confirmer."),
    "validated": ("✅ Validated. Thank you.", "✅ Validé. Merci."),
    "no_page_to_edit": ("There is no page to edit yet.", "Il n'y a pas encore de page à modifier."),
    "which_page": ("Which page?", "Quelle page ?"),
    "btn_back": ("Back", "Retour"),
    "which_photo_retake": ("Which photo do you want to take again?", "Quelle photo voulez-vous reprendre ?"),
    "page_n": ("Page {n}", "Page {n}"),
    "ai_will_read_now": ("OK. I will read the photos. What you entered yourself is kept and will not be overwritten.",
                         "D'accord. Je vais lire les photos. Ce que vous avez saisi vous-même est conservé et ne "
                         "sera pas écrasé."),
    "ai_will_read_later": ("OK. I will read the photos when I can (*Pending AI processing*). "
                           "What you entered yourself is kept and will not be overwritten.",
                           "D'accord. Je lirai les photos dès que possible (*En attente de traitement par l'IA*). "
                           "Ce que vous avez saisi vous-même est conservé et ne sera pas écrasé."),
    "mark.NEEDS_REVIEW": (" ⚠️ to check", " ⚠️ à vérifier"),
    "mark.ILLEGIBLE": (" ❓ illegible", " ❓ illisible"),
    "mark.NOT_PROVIDED": (" (blank)", " (vide)"),
    "mark.NOT_APPLICABLE": (" (not applicable)", " (sans objet)"),
    "mark.UNKNOWN": (" (unknown)", " (inconnu)"),
    "mark.KNOWN": ("", ""),
    "type_add": ("\nType *add* to add a {g}.", "\nTapez *ajouter* pour ajouter : {g}."),
    "type_number": ("\n\nType the NUMBER of the field to change.", "\n\nTapez le NUMÉRO du champ à modifier."),
    # ---- retake --------------------------------------------------------------
    "retake_send": ("📷 Send the new photo of that page. The old photo is kept until the new one is saved.",
                    "📷 Envoyez la nouvelle photo de cette page. L'ancienne photo est conservée jusqu'à "
                    "l'enregistrement de la nouvelle."),
    "retake_waiting": ("I am waiting for the new photo of that page.", "J'attends la nouvelle photo de cette page."),
    "retake_saved_now": ("📄 New photo saved. I am reading it now. Anything you entered or confirmed yourself is kept.",
                         "📄 Nouvelle photo enregistrée. Je la lis maintenant. Tout ce que vous avez saisi ou "
                         "confirmé vous-même est conservé."),
    "retake_saved_later": ("📄 New photo saved. It is *Pending AI processing*. "
                           "Anything you entered or confirmed yourself is kept.",
                           "📄 Nouvelle photo enregistrée. Elle est *En attente de traitement par l'IA*. "
                           "Tout ce que vous avez saisi ou confirmé vous-même est conservé."),
    # ---- follow-up questions ----------------------------------------------------
    "last_question": ("That was the last question.", "C'était la dernière question."),
    "group_add_q": ("*{sec}* - is there a filled column to add ({g})? Choose it, or tap No more.",
                    "*{sec}* - y a-t-il une colonne remplie à ajouter ({g}) ? Choisissez-la, ou touchez Aucune autre."),
    "btn_no_more": ("No more", "Aucune autre"),
    "group_check_q": ("*{sec}* - I read {n} filled column(s) in the table ({g}). Is another column filled on "
                      "the paper? Choose it, or tap No more.",
                      "*{sec}* - J'ai lu {n} colonne(s) remplie(s) dans le tableau ({g}). Une autre colonne "
                      "est-elle remplie sur le papier ? Choisissez-la, ou touchez Aucune autre."),
    "menu_tables": ("One last check on the tables before you can confirm.",
                    "Une dernière vérification des tableaux avant de pouvoir confirmer."),
    # ---- all uncertain fields in one message --------------------------------------
    "btn_see_all": ("📋 See all {n} at once", "📋 Voir les {n} d'un coup"),
    "bulk_head": ("Here are the {n} fields I am not sure about:", "Voici les {n} champs dont je ne suis pas sûr :"),
    "bulk_value": ("{i}. {name}: “{v}” (confidence {conf})", "{i}. {name} : « {v} » (confiance {conf})"),
    "bulk_illegible": ("{i}. {name}: ❓ written, but I cannot read it", "{i}. {name} : ❓ écrit, mais je n'arrive pas à le lire"),
    "bulk_nothing": ("{i}. {name}: I have no value", "{i}. {name} : je n'ai pas de valeur"),
    "bulk_foot": ("\nCompare with the paper. Type the NUMBER of a field to correct it, or use a button.",
                  "\nComparez avec le papier. Tapez le NUMÉRO d'un champ pour le corriger, ou utilisez un bouton."),
    "btn_bulk_ok": ("✓ The {n} values shown are correct", "✓ Les {n} valeurs affichées sont correctes"),
    "btn_bulk_step": ("One by one", "Un par un"),
    "bulk_done": ("OK, {n} value(s) confirmed.", "D'accord, {n} valeur(s) confirmée(s)."),
    "bulk_hint": ("Type a number from 1 to {n} to correct that field, or use a button.",
                  "Tapez un numéro de 1 à {n} pour corriger ce champ, ou utilisez un bouton."),
    "head_line": ("*{sec}* › {title}  (printed: “{label}”)", "*{sec}* › {title}  (imprimé : « {label} »)"),
    "head_line_short": ("*{sec}* › {title}", "*{sec}* › {title}"),
    "always_confirm": ("🔎 I read “{v}” (confidence {conf}). "
                       "I always ask you to confirm this field, whatever my confidence.",
                       "🔎 J'ai lu « {v} » (confiance {conf}). "
                       "Je vous demande toujours de confirmer ce champ, quelle que soit ma confiance."),
    "not_sure": ("⚠️ I read “{v}” but I am not sure (confidence {conf}).",
                 "⚠️ J'ai lu « {v} » mais je ne suis pas sûr (confiance {conf})."),
    "why": ("\nWhy: {note}.", "\nPourquoi : {note}."),
    "conf_unknown": ("unknown", "inconnue"),
    "btn_correct": ("✓ It is correct", "✓ C'est correct"),
    "illegible_body": ("❓ Something is written here but I cannot read it.",
                       "❓ Quelque chose est écrit ici mais je n'arrive pas à le lire."),
    "ai_note": ("⚠️ {note}{conf}.", "⚠️ {note}{conf}."),
    "default_note": ("I am not sure about this field", "Je ne suis pas sûr de ce champ"),
    "conf_paren": (" (confidence {conf})", " (confiance {conf})"),
    "current_value": ("Current value: “{v}”.", "Valeur actuelle : « {v} »."),
    "what_written": ("What is written on the paper?", "Qu'est-ce qui est écrit sur le papier ?"),
    "btn_ticked": ("Ticked", "Cochée"),
    "btn_not_ticked": ("Not ticked", "Non cochée"),
    "btn_none_ticked": ("None ticked", "Aucune cochée"),
    "hint_multi": ("Type the ticked options separated by commas: {opts}.",
                   "Tapez les options cochées séparées par des virgules : {opts}."),
    "hint_date": ("Type the date as dd/mm/yyyy.", "Tapez la date au format jj/mm/aaaa."),
    "hint_bp": ("Type it as 120/80.", "Tapez-la sous la forme 120/80."),
    "hint_number": ("Type the number{unit}.", "Tapez le nombre{unit}."),
    "hint_text": ("Type what is written.", "Tapez ce qui est écrit."),
    "btn_blank": ("Blank on paper", "Vide sur le papier"),
    "btn_unreadable": ("Unreadable on paper", "Illisible sur le papier"),
    "btn_na": ("Not applicable", "Sans objet"),
    "btn_unknown": ("Written as unknown", "Noté comme inconnu"),
    "btn_rest_blank": ("Rest of this page is blank", "Le reste de cette page est vide"),
    "btn_later": ("Finish later", "Terminer plus tard"),
    "btn_skip": ("Skip for now", "Passer pour l'instant"),
    "left": ("\n({n} left)", "\n({n} restant(s))"),
    "retype": ("OK, type it again.", "D'accord, retapez-le."),
    "saved_later": ("Saved. Registry {tag} is waiting for you; type *status* to continue it.",
                    "Enregistré. Le registre {tag} vous attend ; tapez *statut* pour le reprendre."),
    "identifier_removed": ("I removed something that looks like a personal identifier (name, phone, ID or address). "
                           "These are never stored.",
                           "J'ai retiré quelque chose qui ressemble à un identifiant personnel (nom, téléphone, "
                           "numéro d'identité ou adresse). Ces données ne sont jamais enregistrées."),
    "not_understood": ("I could not understand that as a {kind}. Please type it again, or use a button.",
                       "Je n'ai pas compris cette réponse comme {kind}. Veuillez la retaper, ou utiliser un bouton."),
    "out_of_range": ("“{v}” is outside the range I expect for this field. Is it really what is written?",
                     "« {v} » est en dehors de la plage que j'attends pour ce champ. Est-ce bien ce qui est écrit ?"),
    "btn_keep": ("Yes, keep it", "Oui, garder"),
    "btn_retype": ("No, I will retype", "Non, je retape"),
    "use_button": ("Please use one of the buttons, or type the value.",
                   "Veuillez utiliser un des boutons, ou taper la valeur."),
    # ---- manual entry --------------------------------------------------------------
    "manual_not_needed": ("Registry {tag} is {state}; manual entry is not needed.",
                          "Le registre {tag} est {state} ; la saisie manuelle n'est pas nécessaire."),
    "manual_intro": ("✍️ Manual entry. I will ask field by field; use the buttons for blank, unreadable or "
                     "not-applicable cells. I never fill anything in for you.",
                     "✍️ Saisie manuelle. Je vais demander champ par champ ; utilisez les boutons pour les cases "
                     "vides, illisibles ou sans objet. Je ne remplis jamais rien à votre place."),
    "which_registry_page": ("Page {n}: which page of the registry is it?",
                            "Page {n} : de quelle page du registre s'agit-il ?"),
    "choose_page_button": ("Please choose the page with a button.", "Veuillez choisir la page avec un bouton."),
    "nothing_to_retry": ("Nothing to retry.", "Rien à réessayer."),
    "retry_now": ("OK, I will try again.", "D'accord, je réessaie."),
    "retry_later": ("OK, I will try again when the connection is back.",
                    "D'accord, je réessaierai au retour de la connexion."),
    # ---- re-digitisation ---------------------------------------------------------------
    "dup_q": ("⚠️ *Possible duplicate.* A registry with the same code “{code}” was already saved on "
              "{date} (pages: {secs}).\nIs this the same registry photographed again?",
              "⚠️ *Doublon possible.* Un registre portant le même code « {code} » a déjà été enregistré le "
              "{date} (pages : {secs}).\nEst-ce le même registre photographié à nouveau ?"),
    "btn_dup_update": ("Yes - update the saved record", "Oui - mettre à jour le registre enregistré"),
    "btn_dup_different": ("No - a different woman", "Non - une autre femme"),
    "absent": ("(absent)", "(absent)"),
    "dup_kept": ("OK. Pages you did not photograph again are kept from the saved record. "
                 "The saved record itself is not deleted.",
                 "D'accord. Les pages que vous n'avez pas rephotographiées sont reprises du registre enregistré. "
                 "Le registre enregistré lui-même n'est pas supprimé."),
    "dup_diff_head": ("*{sec}*: {n} field(s) differ between the saved record and the new photo.",
                      "*{sec}* : {n} champ(s) diffèrent entre le registre enregistré et la nouvelle photo."),
    "dup_diff_line": ("• {title}: saved {old} → new {new}", "• {title} : enregistré {old} → nouveau {new}"),
    "dup_more": ("… and {n} more.", "… et {n} de plus."),
    "dup_which": ("\nWhich do you want to keep for this page?", "\nQue voulez-vous garder pour cette page ?"),
    "btn_use_new": ("Use the new photo", "Utiliser la nouvelle photo"),
    "btn_keep_saved": ("Keep the saved values", "Garder les valeurs enregistrées"),
    # ---- patient matching ------------------------------------------------------------------
    "d_code": ("code {code}", "code {code}"),
    "d_age": ("age {age}", "âge {age}"),
    "d_lmp": ("LMP {date}", "DDR {date}"),
    "d_records": ("{n} saved registry(ies)", "{n} registre(s) enregistré(s)"),
    "link_code_q": ("🔗 I could not read a registry code (N° de la fiche / code written by you) on these pages. "
                    "Please type the code written on the registry.",
                    "🔗 Je n'ai pas pu lire de code de registre (N° de la fiche / code écrit par vous) sur ces pages. "
                    "Veuillez taper le code écrit sur le registre."),
    "btn_no_code": ("There is no code on the paper", "Il n'y a pas de code sur le papier"),
    "link_which": ("🔗 Which woman is this registry (code {code}) for? I only suggest - *you decide*.",
                   "🔗 À quelle femme correspond ce registre (code {code}) ? Je ne fais que suggérer - "
                   "*c'est vous qui décidez*."),
    "link_cand": ("*Patient {i}*: {desc}\n   why: {reasons}", "*Patiente {i}* : {desc}\n   pourquoi : {reasons}"),
    "btn_patient": ("Patient {i}", "Patiente {i}"),
    "btn_none_new": ("None, create new", "Aucune, créer une nouvelle"),
    "btn_unsure": ("I'm not sure", "Je ne suis pas sûre"),
    "link_none": ("🔗 No saved patient looks like this registry (code {code}). Do you want to create a new patient?",
                  "🔗 Aucune patiente enregistrée ne ressemble à ce registre (code {code}). "
                  "Voulez-vous créer une nouvelle patiente ?"),
    "btn_create_new": ("Create new patient", "Créer une nouvelle patiente"),
    "type_code": ("Please type the code, or tap the button.", "Veuillez taper le code, ou toucher le bouton."),
    "bad_code": ("That does not look like a registry code. A code must not be a name, phone or ID number.",
                 "Cela ne ressemble pas à un code de registre. Un code ne doit pas être un nom, un téléphone ou "
                 "un numéro d'identité."),
    "unsure_saved": ("No problem. The registry is saved but NOT attached to any patient yet "
                     "(*manual review required*). Nothing was created.",
                     "Pas de problème. Le registre est enregistré mais n'est PAS encore rattaché à une patiente "
                     "(*vérification manuelle requise*). Rien n'a été créé."),
    "btn_decide_now": ("Decide now", "Décider maintenant"),
    "registered_sync": ("✅ Registered. This patient now has {n} registry record(s) in her profile. Synchronising…",
                        "✅ Enregistré. Cette patiente a maintenant {n} registre(s) dans son profil. Synchronisation…"),
    "registered_offline": ("✅ Registered. This patient now has {n} registry record(s) in her profile. "
                           "📴 No connection: it will be synchronised when the connection returns.",
                           "✅ Enregistré. Cette patiente a maintenant {n} registre(s) dans son profil. "
                           "📴 Pas de connexion : il sera synchronisé au retour de la connexion."),
    "reason.same code": ("same code", "même code"),
    "reason.similar code": ("similar code", "code similaire"),
    "reason.same last menstrual period date": ("same last menstrual period date", "même date des dernières règles"),
    "reason.same age (within 1 year)": ("same age (within 1 year)", "même âge (à 1 an près)"),
    "reason.same province": ("same province", "même province"),
    "reason.the record you are updating": ("the record you are updating", "le registre que vous mettez à jour"),
    # ---- reports ----------------------------------------------------------------------------
    "no_registry_yet": ("No registry yet. ", "Aucun registre pour l'instant. "),
    "status_head": ("Connection: {net} · AI: {ai}", "Connexion : {net} · IA : {ai}"),
    "online": ("online", "en ligne"),
    "offline": ("📴 offline", "📴 hors ligne"),
    "ai_available": ("available", "disponible"),
    "ai_not_available": ("not available", "non disponible"),
    "status_line": ("• {tag} - {state} - {n} page(s)", "• {tag} - {state} - {n} page(s)"),
    "btn_open": ("Open {tag}", "Ouvrir {tag}"),
    "status_sync": ("Waiting to synchronise: {n}", "En attente de synchronisation : {n}"),
    "no_patient": ("No patient yet.", "Aucune patiente pour l'instant."),
    "history_patient": ("*Patient {i}*: {desc}", "*Patiente {i}* : {desc}"),
    "history_visit": ("      antenatal visit ({slot}): {date}", "      consultation prénatale ({slot}) : {date}"),
    # ---- value words ---------------------------------------------------------------------------
    "ticked": (S.TICKED, "cochée"),
    "not_ticked": (S.NOT_TICKED, "non cochée"),
    "none_ticked": ("none ticked", "aucune cochée"),
    "status.KNOWN": ("known", "connu"),
    "status.UNKNOWN": ("unknown", "inconnu"),
    "status.NOT_PROVIDED": ("not provided", "non renseigné"),
    "status.ILLEGIBLE": ("illegible", "illisible"),
    "status.NOT_APPLICABLE": ("not applicable", "sans objet"),
    "status.NEEDS_REVIEW": ("needs review", "à vérifier"),
    "kind.text": ("text", "un texte"), "kind.int": ("int", "un nombre entier"),
    "kind.number": ("number", "un nombre"), "kind.date": ("date", "une date"),
    "kind.bool": ("bool", "une case à cocher"), "kind.choice": ("choice", "un choix"),
    "kind.multi": ("multi", "une liste d'options"), "kind.bp": ("bp", "une tension artérielle"),
}


def norm_lang(lang) -> str:
    return lang if lang in LANGS else DEFAULT_LANG


def t(lang: str, key: str, **kw) -> str:
    """The message `key` in `lang`, with {placeholders} filled in."""
    text = M[key][LANGS.index(norm_lang(lang))]
    return text.format(**kw) if kw else text


# ----------------------------------------------------------------------------
# Names of pages, columns and fields
# ----------------------------------------------------------------------------
_GROUP_FR = {"visits": "Consultation prénatale", "previous_deliveries": "Accouchement antérieur"}
_SLOT_FR = {"t1_v1": "1er trimestre, visite 1", "t1_v2": "1er trimestre, visite 2", "t1_v3": "1er trimestre, visite 3",
            "t2_v1": "2e trimestre, visite 1", "t2_v2": "2e trimestre, visite 2", "t2_v3": "2e trimestre, visite 3",
            "m7": "7e mois", "m8": "8e mois", "m9": "9e mois"}
_ANOMALY_COL_FR = {"count": "nombre", "date": "date", "place": "lieu", "gestational_age_weeks": "âge gestationnel (SA)"}

# French display names where the printed label alone would be ambiguous (the same
# label is printed twice on the page) or too terse out of context.  Every other
# field is shown under its printed label, which is what the midwife reads on paper.
_FIELD_FR = {
    ("cover", "fiche_number"): "N° de la fiche / code",
    ("cover", "risk_types"): "Types de risque cochés",
    ("identification", "tetanus_doses"): "VAT (doses cochées)",
    ("identification", "rubella_vaccine_date"): "Date du vaccin contre la rubéole",
    ("identification", "hepatitis_b_vaccine_date"): "Date du vaccin contre l'hépatite B",
    ("identification", "history_medical"): "Antécédents médicaux",
    ("identification", "history_surgical"): "Antécédents chirurgicaux",
    ("identification", "history_gynecological"): "Antécédents gynécologiques",
    ("pregnancy", "lmp_date"): "DDR (date des dernières règles)",
    ("pregnancy", "expected_delivery_date"): "Date prévue d'accouchement",
    ("pregnancy", "term_overrun_date"): "Date de dépassement de terme",
    ("delivery", "place_setting"): "Lieu de l'accouchement",
    ("delivery", "supervised_place"): "Milieu surveillé (type)",
    ("delivery", "instrumental_details"): "Voie basse instrumentale (détails)",
    ("delivery", "cesarean_indication"): "Indication de la césarienne",
    ("delivery", "complication_timing"): "Moment des complications",
    ("delivery", "newborn_sex"): "Sexe du nouveau-né",
    ("pp_mother", "perineum_repaired"): "Périnée : réparée",
    ("pp_mother", "complications"): "Complications (type)",
    ("pp_mother", "medication_intake"): "Prise de médicaments",
    ("pp_mother", "medication_details"): "Médicaments pris (tel qu'écrit)",
    ("pp_mother", "fp_method"): "Planification familiale : méthode",
    ("pp_mother", "fp_reason_no_method"): "Raison du refus d'une méthode contraceptive",
    ("pp_newborn", "age"): "Âge du nouveau-né",
    ("pp_newborn", "danger_signs_other"): "Autres signes à préciser",
    ("pp_newborn", "trauma_other"): "Autres lésions ou malformations à préciser",
}


def _clean_label(label: str) -> str:
    return label.strip().rstrip(":").strip()


def _family(section_key: str) -> str:
    """The early and late postpartum pages share one layout: one set of names for both."""
    if section_key.startswith("pp_"):
        return "pp_mother" if section_key.endswith("mother") else "pp_newborn"
    return section_key


def section_title(lang: str, sec: S.Section) -> str:
    return sec.title_fr if norm_lang(lang) == "fr" else sec.en


def group_title(lang: str, g: S.RepeatGroup) -> str:
    return _GROUP_FR.get(g.name, g.en) if norm_lang(lang) == "fr" else g.en


def slot_title(lang: str, slot: str, default: str | None = None) -> str:
    table = _SLOT_FR if norm_lang(lang) == "fr" else S.SLOT_TITLES
    return table.get(slot, default if default is not None else f"#{slot}")


def field_title(lang: str, section_key: str, f: S.Field) -> str:
    if norm_lang(lang) != "fr":
        return f.title
    special = _FIELD_FR.get((_family(section_key), f.key))
    if special:
        return special
    if section_key == "identification":
        if f.key.startswith("family_"):
            side = "famille de la femme" if f.key.endswith("_woman") else "mari / famille"
            return f"Antécédents familiaux - {_clean_label(f.label)} ({side})"
        for col, name in _ANOMALY_COL_FR.items():
            if f.key.endswith("_" + col) and f.loc[:1] == ("cell",):
                row = "Autres anomalies" if f.key.startswith("other_anomaly") else _clean_label(f.label)
                return f"{row} - {name}"
    return _clean_label(f.label)


def path_title(lang: str, section_key: str, path: str) -> str:
    """Display name of a field path such as `age` or `visits[t1_v2].weight_kg`."""
    if norm_lang(lang) != "fr":
        return S.path_title(section_key, path)
    sec = S.SECTION_BY_KEY[section_key]
    if "[" in path:
        gname, rest = path.split("[", 1)
        slot, key = rest.split("].", 1)
        g = next(x for x in sec.groups if x.name == gname)
        f = next(x for x in g.fields if x.key == key)
        return f"{group_title(lang, g)} {slot_title(lang, slot)} - {_clean_label(f.label)}"
    return field_title(lang, section_key, sec.field(path))


def printed_label(f: S.Field) -> str:
    return f.label


def shows_printed_label(lang: str, section_key: str, path: str, f: S.Field) -> bool:
    """In French the display name usually IS the printed label: do not repeat it."""
    return norm_lang(lang) != "fr" or _clean_label(f.label) not in path_title(lang, section_key, path)


def value(lang: str, f: S.Field, v) -> str:
    """A stored value as shown to the midwife.  Only the two checkbox words are
    translated; everything else is shown as written (see normalize.display)."""
    from .normalize import display
    if v == S.TICKED:
        return t(lang, "ticked")
    if v == S.NOT_TICKED:
        return t(lang, "not_ticked")
    if v == []:
        return t(lang, "none_ticked")
    shown = display(f, v)
    if f.unit and unit(lang, f.unit) != f.unit and shown.endswith(" " + f.unit):
        shown = shown[:-len(f.unit)] + unit(lang, f.unit)
    return shown


_UNIT_FR = {"years": "ans"}          # the other units (cm, kg, g, SA, bpm, °C, g/dL, g/L) read the same in French


def unit(lang: str, name: str) -> str:
    return _UNIT_FR.get(name, name) if norm_lang(lang) == "fr" else name


# ----------------------------------------------------------------------------
# Notes attached to a field by the pipeline (stored in English, shown translated)
# ----------------------------------------------------------------------------
_NOTE_FR = {
    "a possible personal identifier was removed": "un possible identifiant personnel a été retiré",
    "the value is outside the expected range": "la valeur est en dehors de la plage attendue",
    "low confidence": "confiance faible",
    "always confirmed with you (linking code or infection test result)":
        "toujours confirmé avec vous (code de liaison ou résultat de test d'infection)",
    "empty value": "valeur vide",
    "written but I cannot read it": "écrit mais je n'arrive pas à le lire",
    "the AI returned an invalid status": "l'IA a renvoyé un statut invalide",
    "the AI did not return this field": "l'IA n'a pas renvoyé ce champ",
    "not returned by the AI": "non renvoyé par l'IA",
    "looks blank, but I am not sure": "semble vide, mais je ne suis pas sûr",
    "looks struck through, but I am not sure": "semble barré, mais je ne suis pas sûr",
    "looks marked unknown, but I am not sure": "semble noté comme inconnu, mais je ne suis pas sûr",
    "parity is greater than gravidity - please check both":
        "la parité est supérieure à la gestation - veuillez vérifier les deux",
    "the two dates are not about 40 weeks apart - please check both":
        "les deux dates ne sont pas séparées d'environ 40 semaines - veuillez vérifier les deux",
    "confirmed by midwife": "confirmé par la sage-femme",
    "unreadable on the paper itself": "illisible sur le papier lui-même",
    "outside expected range, confirmed by midwife": "hors de la plage attendue, confirmé par la sage-femme",
    "declared blank by midwife": "déclaré vide par la sage-femme",
}
_NOT_INTERPRETED = re.compile(r"^I could not interpret this as a (\w+)$")


def note(lang: str, text: str) -> str:
    """Translate a pipeline note ('low confidence; the value is outside ...')."""
    if norm_lang(lang) != "fr" or not text:
        return text or ""
    out = []
    for part in text.split("; "):
        m = _NOT_INTERPRETED.match(part)
        if m and f"kind.{m.group(1)}" in M:
            out.append("je n'ai pas pu interpréter ceci comme " + t("fr", f"kind.{m.group(1)}"))
        else:
            out.append(_NOTE_FR.get(part, part))
    return "; ".join(out)


def reasons(lang: str, items) -> str:
    """Why a patient is suggested as a match (linking.candidates reasons)."""
    return ", ".join(t(lang, f"reason.{r}") if f"reason.{r}" in M else r for r in items)
