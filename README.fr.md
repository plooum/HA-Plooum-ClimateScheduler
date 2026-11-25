# Plooum Climate Scheduler

*[English version](README.md)*

Programmation hebdomadaire des thermostats (entités `climate`) de Home Assistant, avec des **profils** communs à toute la maison (Défaut, Vacances…).

- Un **programme** pilote un ou plusieurs thermostats. Pour chaque profil, il a une semaine : pour chaque jour, les heures où commence une température (ou *éteint*). Un programme sans semaine pour le profil actif utilise sa semaine Défaut.
- Le **profil** actif est une entité `select` : passez toute la maison en *Vacances* depuis un tableau de bord ou une automatisation.
- Un thermostat changé à la main garde son réglage **jusqu'au prochain changement** de sa programmation, puis la programmation reprend la main.

Programmes et profils se modifient dans la **carte de programmation** de [HA-Plooum-Cards](https://github.com/plooum/HA-Plooum-Cards) (bientôt).

## Installation

1. HACS → Intégrations → ⋮ → Dépôts personnalisés → `https://github.com/plooum/HA-Plooum-ClimateScheduler`, catégorie *Intégration*. Installez **Plooum Climate Scheduler** et redémarrez Home Assistant.
2. Paramètres → Appareils et services → Ajouter une intégration → **Plooum Climate Scheduler**. Il n'y a rien à régler.

## Entités

| Entité | Rôle |
| :--- | :--- |
| `select.climate_scheduler_profile` | le profil actif de la maison |
| `switch.<programme>_schedule` | active ou désactive un programme ; désactivé, il ne touche plus à ses thermostats |
| `sensor.<programme>_scheduled_temperature` | la température du créneau en cours (vide quand le créneau est *éteint*). Attributs : `scheduled`, `profile` (la semaine utilisée), `next_change`, `next_value`, `overrides` (thermostats changés à la main, avec leur valeur et jusqu'à quand), `climate_entities` |

## Actions

| Action | Données |
| :--- | :--- |
| `plooum_climate_scheduler.set_profile` | `profile` : nom du profil |
| `plooum_climate_scheduler.clear_override` | `entity_id` (facultatif) : thermostats changés à la main à rendre tout de suite à leur programmation ; aucun : tous |

```yaml
# Vacances tant que tout le monde est absent
action: plooum_climate_scheduler.set_profile
data:
  profile: Vacances
```

## Fonctionnement

- La température du créneau en cours est appliquée au début de chaque créneau, au démarrage de Home Assistant, à l'enregistrement ou à l'activation d'un programme, et au changement de profil. Les créneaux passés ne sont pas rejoués.
- Un créneau *éteint* éteint les thermostats (`hvac_mode: off`) ; le créneau en température suivant les rallume (dans leur mode précédent, sinon `heat`).
- Les températures restent entre le minimum et le maximum du thermostat.
- Un thermostat indisponible reçoit le créneau en cours dès qu'il revient.
- **Paramètres de créneau** : un programme peut lister des interrupteurs (`switch`, `input_boolean`) et des nombres (`number`, `input_number`) que ses créneaux règlent aussi, comme le mode éco et l'hystérésis d'un poêle à pellets. Une valeur tient jusqu'au créneau qui la change, comme la température ; le programmateur les règle à chaque créneau et les remet au changement suivant s'ils ont été changés à la main.
- **Thermostats qui s'éteignent tout seuls** (mode éco d'un poêle à pellets, veille) : cochez *Le thermostat s'éteint tout seul* dans les réglages du programme. Le programmateur ne rallume alors que ce qu'il a éteint lui-même (créneau Éteint) ; un thermostat éteint de lui-même reçoit seulement sa nouvelle consigne au changement de créneau et se rallume seul, et son extinction n'est pas un changement manuel.
- Un changement manuel, c'est la température de consigne, ou éteindre / allumer le thermostat ; changer son mode (chauffage → auto) n'en est pas un. Les changements signalés dans les 60 secondes qui suivent une commande du programmateur ne sont comparés qu'à la fin de ce délai (certains thermostats renvoient d'abord un état périmé).
- Les changements manuels survivent à un redémarrage. Changer de profil, enregistrer ou activer un programme, et `clear_override` y mettent fin.
- Les heures suivent le fuseau de Home Assistant. La nuit du passage à l'heure d'été, un changement placé dans l'heure sautée (02:30) a lieu une heure plus tard.

## Développement

La carte parle au moteur par l'API websocket décrite dans [docs/CONCEPTION.md](docs/CONCEPTION.md). Tests : `python -m unittest discover tests` ; un Home Assistant de développement avec trois faux thermostats : `dev/ha.sh start`, scénario de bout en bout : `dev/scenario.py`.
