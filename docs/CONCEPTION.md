# Conception — Plooum Climate Scheduler

Document de travail, en français (conventions des dépôts Plooum : tout en français sauf le code et la documentation utilisateur). Décisions prises le 2026-11-25.

## Besoin

Programmer la température d'entités `climate` de Home Assistant selon l'heure et le jour de la semaine, avec des **profils** valables pour toute la maison (Défaut, Vacances…). L'interface est une carte de HA-Plooum-Cards : un bouton ouvre un calendrier de la semaine où l'on dessine des créneaux, avec copier / coller d'un ensemble de créneaux ou d'une journée.

Le moteur doit tourner côté serveur (une carte ne s'exécute que quand un tableau de bord est ouvert) : c'est cette intégration. La carte vit dans HA-Plooum-Cards et ne parle au moteur que par son API websocket.

## Vocabulaire

| Terme | Anglais (code) | Sens |
| :--- | :--- | :--- |
| Programme | program | un ensemble de thermostats (`climate`) pilotés ensemble, avec une semaine par profil |
| Profil | profile | un mode de toute la maison (Défaut, Vacances…) ; un seul est actif à la fois |
| Semaine | week | 7 jours, chacun une liste de points |
| Point | point | `{heure, valeur}` : la valeur tient jusqu'au point suivant |
| Créneau | slot | l'intervalle entre deux points, tel que la carte le montre |
| Dérogation | override | un changement fait à la main sur un thermostat, respecté jusqu'au prochain créneau |

## Modèle

```json
{
  "profiles": [
    { "id": "default", "name": "Default" },
    { "id": "3f9c1a2b7d4e", "name": "Vacances" }
  ],
  "active_profile": "default",
  "palette": [15, 17, 19, 21],
  "programs": [
    {
      "id": "a1b2c3d4e5f6",
      "name": "Salon",
      "entities": ["climate.salon"],
      "enabled": true,
      "hvac_mode": null,
      "self_off": false,
      "profiles": {
        "default": {
          "mon": [ { "at": "00:00", "value": 17 }, { "at": "17:00", "value": 21 } ],
          "tue": [ ... ]
        },
        "3f9c1a2b7d4e": {
          "mon": [ { "at": "00:00", "value": 17 }, { "at": "09:00", "value": 21 } ]
        }
      }
    }
  ]
}
```

- **Palette** : les températures proposées d'abord par la carte (15, 17, 19, 21 au départ), extensible depuis la carte et commune à toute la maison. Ce n'est qu'un raccourci : un créneau peut prendre n'importe quelle température, et changer la palette ne change aucun créneau.
- **Profils globaux** : la liste est commune à tous les programmes. Le profil `default` existe toujours (on peut le renommer, pas le supprimer).
- **Une semaine par profil et par programme**. Si un programme n'a **pas** de semaine pour le profil actif, il utilise sa semaine `default`. Supprimer un profil supprime ses semaines dans tous les programmes ; si c'était le profil actif, `default` le devient.
- **Fonction en escalier** : chaque point vaut jusqu'au point suivant, il n'y a ni trou ni chevauchement. Le dernier point d'un jour tient jusqu'au premier point du jour suivant ; un jour sans point continue donc la veille. La carte pose toujours un point à `00:00`, mais le moteur ne l'exige pas.
- **Valeur** : une température (dans l'unité de HA) ou `"off"` (thermostat éteint : `hvac_mode: off`).
- **Un thermostat n'appartient qu'à un programme** (refusé à l'enregistrement sinon).
- `self_off` (faux par défaut, décision du 2026-11-25 pour un poêle en mode éco) : les thermostats du programme s'éteignent et se rallument d'eux-mêmes (veille). Le moteur ne rallume alors que ceux qu'il a éteints lui-même (créneau `off`, mémorisé et persistant) ; un thermostat éteint d'une autre façon ne reçoit que sa consigne au changement de créneau, sans `hvac_mode`, et son passage à éteint n'est pas une dérogation. Une consigne changée à la main reste une dérogation.
- `hvac_mode` (facultatif) : le mode à mettre quand un créneau en température suit un créneau `off` ou trouve le thermostat éteint. Par défaut : le mode actuel s'il n'est pas `off`, sinon `heat`, `heat_cool` ou `auto` (le premier que le thermostat accepte).
- Heures `HH:MM` à la minute ; la carte travaille au pas de 15 min.

## Moteur

Pour chaque programme activé :

- **Au changement de créneau** (minuterie calée sur le prochain point dont la valeur diffère), au démarrage de HA (une fois HA démarré, pour que les thermostats existent), à l'enregistrement du programme, au changement de profil et à la réactivation : il applique la valeur du créneau en cours à chaque thermostat. Pas de rattrapage des créneaux passés.
- **Appliquer** : `climate.set_temperature` (avec `hvac_mode` si le thermostat est éteint), ou `climate.set_hvac_mode: off`. Rien n'est envoyé si le thermostat est déjà dans l'état voulu. La température est ramenée entre `min_temp` et `max_temp` du thermostat.
- **Thermostat indisponible** : ignoré ; la valeur du créneau lui est appliquée dès qu'il revient.
- **Changement d'heure** : les heures sont celles du fuseau de HA ; un point dans l'heure qui n'existe pas (printemps, 02:30) s'applique une heure plus tard (03:30).

### Dérogations

Un changement de la consigne ou un passage à `off` / hors `off` d'un thermostat qui ne vient pas du moteur (contexte différent) est une **dérogation** : le moteur la respecte **jusqu'au prochain changement de créneau** (heure affichée par la carte, par exemple « manuel jusqu'à 00:00 »), puis reprend la main.

- Les changements de mode (`heat` → `auto`) ou d'`hvac_action` ne sont pas des dérogations : seuls comptent la consigne et éteint / allumé.
- Certains thermostats renvoient d'abord un état périmé après une commande : pendant 60 s après une commande, le moteur attend la fin de ce délai et ne compare qu'alors l'état au demandé.
- Une dérogation qui revient à la valeur programmée disparaît.
- Changer de profil, enregistrer ou réactiver le programme, ou l'action `clear_override` annulent les dérogations.
- Sur une semaine constante (aucun changement), une dérogation dure jusqu'à son annulation.
- Les dérogations survivent à un redémarrage de HA.

## Entités

| Entité | Rôle |
| :--- | :--- |
| `select.climate_scheduler_profile` | le profil actif de la maison (pour les automatisations : partir en vacances) |
| `switch.<programme>_schedule` | active / désactive un programme ; désactivé, il ne touche plus à ses thermostats |
| `sensor.<programme>_scheduled_temperature` | la consigne du créneau en cours (vide si `off`) ; attributs : `scheduled` (température ou `off`), `profile`, `next_change`, `next_value`, `overrides`, `climate_entities`, `program_id` |

Les entités d'un programme sont créées / supprimées avec lui ; un appareil par programme.

## Actions

- `plooum_climate_scheduler.set_profile` : `profile` (nom ou id).
- `plooum_climate_scheduler.clear_override` : `entity_id` (thermostats, facultatif : tous).

## API websocket (pour la carte)

| Commande | Rôle |
| :--- | :--- |
| `plooum_climate_scheduler/subscribe` | renvoie `{config, status}` puis un événement à chaque changement |
| `plooum_climate_scheduler/get` | `{config, status}` une fois |
| `plooum_climate_scheduler/program/save` | crée (sans `id`) ou remplace un programme ; renvoie le programme |
| `plooum_climate_scheduler/program/delete` | `program_id` |
| `plooum_climate_scheduler/profiles/save` | la liste complète des profils (`id` pour les existants, sans `id` pour les nouveaux) |
| `plooum_climate_scheduler/profile/activate` | `profile_id` |
| `plooum_climate_scheduler/palette/save` | la liste des températures de la palette (triée, sans doublon) |
| `plooum_climate_scheduler/override/clear` | `program_id`, `entity_id` facultatifs |

`status` donne par programme : `profile` (la semaine utilisée), `value`, `next_change`, `next_value`, `overrides` (`{entity_id: {value, until}}`).

Pas de restriction aux administrateurs : comme pour un thermostat, tout utilisateur peut programmer.

## Configuration

Une seule entrée, sans réglage : tout se fait depuis la carte (ou l'API).

## Tests

- `tests/test_schedule.py` : validation, valeur en cours, prochain changement, passage de semaine, changement d'heure (pur, sans HA).
- `dev/` : un HA Core local (port 8132, venv de HA-Plooum-Cards) avec trois thermostats `generic_thermostat` (salon, chambre 1, chambre 2), et `dev/scenario.py` qui joue le moteur de bout en bout par l'API.

## Hors périmètre (pour plus tard)

- Dérogation temporaire choisie (« 22 °C pendant 2 h »).
- Consignes doubles (`target_temp_low` / `high`).
- Un programmateur de poêle à pellets, à refaire plus tard sur le modèle de ce dépôt.
