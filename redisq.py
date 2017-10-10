import requests

import config
import esi

from datetime import datetime


def check_affiliation(character, affiliations):
    """
    Check if character is a member of a list of corps and / or alliances
    :param character: Character object from RedisQ
    :param affiliations: List of Corp- or Alliance IDs
    :return: True if character is affiliated with any of the IDs, False otherwise
    """
    result = False

    result |= character.get('alliance_id', False) in affiliations
    result |= character.get('corporation_id', False) in affiliations

    return result


def fetch_new_kills(queueID, ttw):
    """
    Refresh kills from RedisQ. Loops request until a null package is received.
    :param queueID: ID used to identify client on RedisQ
    :param ttw: Time in seconds to wait before receiving a Null package
    :return: List of kill objects
    """
    url = config.REDISQ_URL.format(queueID=queueID, ttw=ttw)

    kills = []

    while True:
        r = requests.get(url).json()
        if r['package'] == None:
            break

        kills.append(r['package'])

    return kills


def fetch_kill(queueID, ttw):
    """
    Fetch a single kill from RedisQ.
    :param queueID: ID used to identify client on RedisQ
    :param ttw: Time in seconds to wait before receiving a Null package
    :return: Kill object
    """
    url = config.REDISQ_URL.format(queueID=queueID, ttw=ttw)

    r = requests.get(url)

    if r.status_code == 200:
        return r.json().get('package', None)
    else:
        r.raise_for_status()


def filter_affiliation(kill):
    """
    Filter method to check whether any characters associated with the kill are in the affiliations list.
    Intended to be used with `filter()`
    :param kill: Kill object from RedisQ
    :return: True if the victim or any attacker are in any of the alliances or corps passed, False otherwise
    """
    affiliations = config.AFFILIATIONS
    result = False

    killmail = kill.get('killmail', {})

    result |= check_affiliation(killmail.get('victim', {}), affiliations)

    for attacker in killmail.get('attackers', []):
        result |= check_affiliation(attacker, affiliations)

    return result


def format_system(system_id):
    """
    Determines whether a System is in Wormhole space or Known space and produces a formatted location field accordingly
    :param system_id: ID of the system to be formatted
    :return: Text content for the System field in the killmail response
    """
    region = esi.get_system_region(system_id)
    wspace = esi.check_jspace(system_id)
    solar_system_details = esi.get_system(system_id)

    if not wspace:
        system = "<https://zkillboard.com/system/{solar_system[system_id]}|{solar_system[name]}> " \
                 "({solar_system[security_status]:.2f})/ " \
                 "<https://zkillboard.com/region/{region[region_id]}|{region[name]}>".format(region=region,
                                                                                             solar_system=solar_system_details)
    else:
        system = "<https://zkillboard.com/system/{solar_system[system_id]}|{solar_system[name]}> ({wspace})/ " \
                 "<https://zkillboard.com/region/{region[region_id]}|{region[name]}>".format(
            solar_system=solar_system_details,
            region=region,
            wspace=wspace)

    return system


def get_party_details(parties):
    """
    Get character info, substitute char name with ship type for nameless NPCs
    Corp name substituted with faction default corp if corp missing and faction available.
    both set to Unknown if neither of the above apply.
    :param parties: iterable of
    :return:
    """
    for party in parties:
        if 'character_id' in party:
            party['details'] = esi.get_character(party.get('character_id'))
            party['zkb_link'] = 'https://zkillboard.com/character/{}'.format(party.get('character_id'))
        elif 'ship_type_id' in party:
            party['details'] = esi.get_name(party.get('ship_type_id'))
            party['zkb_link'] = 'https://zkillboard.com/ship/{}'.format(party.get('ship_type_id'))
        else:
            party['details'] = {'name': 'Unknown'}
            party['zkb_link'] = '#'

        if 'corporation_id' in party:
            party['corporation'] = esi.get_corporation(party.get('corporation_id'))
            party['corp_zkb_link'] = 'https://zkillboard.com/corporation/{}'.format(party.get('corporation_id'))
        elif 'faction_id' in party:
            party['corporation'] = esi.get_faction_corp(party.get('faction_id'))
            party['corp_zkb_link'] = 'https://zkillboard.com/corporation/{}'.format(
                party['corporation'].get('corporation_id'))
        else:
            party['corporation'] = {'corporation_name': 'Unknown'}
            party['corp_zkb_link'] = '#'

    return parties


def format_kill(kill):
    """
    Format the kill for display on slack
    :param kill: Kill object from RedisQ
    :return: Dictionary that can be dumped into json format codes for slack message
    """
    killID = kill['killID']
    killmail = kill.get('killmail', {})

    victim = killmail.get('victim', {})
    loss = check_affiliation(victim, config.AFFILIATIONS)
    attackers = killmail.get('attackers', [])
    killer = [a for a in attackers if a['final_blow']][0]
    max_dmg = \
        [a for a in attackers if a.get('damage_done', 0) == max([atk.get('damage_done', 0) for atk in attackers])][0]

    zkb = kill.get('zkb', {})

    victim_ship = esi.get_name(victim.get('ship_type_id'))

    if victim_ship:
        ship_text = '<https://zkillboard.com/ship/{id}|{name}>'.format(**victim_ship)
    else:
        ship_text = 'Unknown'



    victim, killer, max_dmg = get_party_details((victim, killer, max_dmg))

    system = format_system(killmail.get('solar_system_id'))

    if loss:
        title = "<{victim[zkb_link]}|{victim[details][name]}> was killed by <{killer[zkb_link]}|{killer[details][name]}> " \
                "(<{killer[corp_zkb_link]}|{killer[corporation][corporation_name]}>)"
    else:
        title = "<{killer[zkb_link]}|{killer[details][name]}> killed <{victim[zkb_link]}|{victim[details][name]}> " \
                "(<{victim[corp_zkb_link]}|{victim[corporation][corporation_name]}>)"

    json = {
        "attachments": [
            {
                "fallback": "https://zkillboard.com/kill/{}/".format(killID),
                "color": (config.COLOR_LOSS if loss else config.COLOR_KILL),
                "title": title.format(killer=killer, victim=victim),
                "fields": [
                    {
                        "title": "Damage taken",
                        "value": "{:,.0f}".format(victim.get('damage_taken', 0)),
                        "short": True
                    },
                    {
                        "title": "Pilots involved",
                        "value": "{:,.0f}".format(len(attackers)),
                        "short": True
                    },
                    {
                        "title": "Value",
                        "value": '{:,.2f}'.format(zkb.get('totalValue')),
                        "short": True
                    },
                    {
                        "title": "Ship",
                        "value": ship_text,
                        "short": True
                    },
                    {
                        "title": "Most damage",
                        "value": "<{most_dmg[zkb_link]}|{most_dmg[details][name]}>"
                                 " ({most_dmg[damage_done]:,.0f})".format(most_dmg=max_dmg),
                        "short": False
                    },
                    {
                        "title": "System",
                        "value": system,
                        "short": False
                    }
                ],
                "thumb_url": "https://imageserver.eveonline.com/Render/{ship_type_id}_64.png".format(
                    ship_type_id=victim.get('ship_type_id', {})),
                "footer": "<https://zkillboard.com/kill/{}|View on zkillboard.com>".format(killID),
                "footer_icon": "https://zkillboard.com/img/wreck.png",
                "ts": datetime.strptime(killmail.get('killmail_time'), '%Y-%m-%dT%H:%M:%SZ').timestamp()
            }
        ]
    }

    return json
