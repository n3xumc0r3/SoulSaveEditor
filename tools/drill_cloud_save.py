#!/usr/bin/env python3
# Focused drill-down into the cloud save JSON.
import json

DL = '/home/z/my-project/download'
obj = json.load(open(DL + '/SoulKnightCloud_pretty.json', encoding='utf-8'))

g = obj['gameData']
it = obj['itemData']
st = obj['statisticData']
sd = obj['seasonData']

print('=== GEM LEDGER (anti-cheat relevant) ===')
for k in ['gems', 'lastGems', 'varGems', 'cumulativeGems', 'cumulativeConsumeGems', 'rebornCard']:
    print('  %-24s %r' % (k, g.get(k)))
print('  seasonData.coin          %r' % sd.get('coin'))
print('  statisticData.KrppVerifiedUsdCents %r' % st.get('KrppVerifiedUsdCents'))
print('  statisticData.KrppCountedOrderIds  %r' % st.get('KrppCountedOrderIds'))

print()
print('=== HEROES ===')
hu = g['heroUnlock']
lvl = g['heroLevel']
skl = g['heroSkill']
print('heroUnlock heroes: %d; unlocked:' % len(hu), sorted(hu.keys()))
print('  values:', sorted(set(hu.values())))
hl_vals = dict(lvl)
print('heroLevel sample:')
for h in sorted(hl_vals.keys())[:8]:
    print('  %-12s %s' % (h, hl_vals[h]))
print('petUnlock: %d pets' % len(g['petUnlock']))
print('superHeroInHeroRoom:', g.get('superHeroInHeroRoom'))
print('totalIndex:', g.get('totalIndex'), ' gameVersion:', g.get('gameVersion'), ' lastPlayTime:', g.get('lastPlayTime'))

print()
print('=== SAVE SLOTS / MODES ===')
print('mode2SaveGameIdDic:', json.dumps(st.get('mode2SaveGameIdDic'), ensure_ascii=False))
m = obj['maps']['maps']
print('maps: %d slot(s); slot[0] keys:' % len(m), list(m[0].keys()) if m else None)
if m:
    s0 = m[0]
    for k, v in s0.items():
        if isinstance(v, (dict, list)):
            print('  %-22s %s (len=%d)' % (k, type(v).__name__, len(v)))
        else:
            print('  %-22s %r' % (k, v))

print()
print('=== KEYS / MATERIALS ===')
rk = it['restoreKeys']
print('restoreKeys: %d items, type=%s, sample=%s' % (len(rk), type(rk[0]).__name__, rk[:5] if rk else None))
usk = g['usedSecretKeys']
print('usedSecretKeys: %d items, type=%s, sample=%s' % (len(usk), type(usk[0]).__name__, usk[:5] if usk else None))
cert = g['certificates']
print('certificates: %d items, sample=%s' % (len(cert), cert[:3]))
print('materials: %d types, top values:' % len(it['materials']))
mm = it['materials']
for k in sorted(mm, key=lambda k: -mm[k])[:12]:
    print('  %-30s %s' % (k, mm[k]))
print('blueprints: %d types' % len(it['blueprints']))
mw = it['mythicWeapons']
print('mythicWeapons: %d items' % len(mw))
for w in mw[:3]:
    print('  ', json.dumps(w, ensure_ascii=False)[:150])
print('seeds: %d types' % len(it['seeds']))
print('tokenTickets: %d types' % len(it['tokenTickets']))

print()
print('=== STATISTICS HIGHLIGHTS ===')
ek = st['_enemyKilledCount']
print('_enemyKilledCount: %d enemy types, top5:' % len(ek))
for k in sorted(ek, key=lambda k: -ek[k])[:5]:
    print('  %-40s %s' % (k, ek[k]))
print('achieve_record: %d items' % len(st['achieve_record']))
print('_multiGameHistoryList:', json.dumps(st.get('_multiGameHistoryList'), ensure_ascii=False)[:200])
print('_pass_history_list: %d items' % len(st.get('_pass_history_list', [])))
ot = st['object2ObtainTime']
print('object2ObtainTime: %d entries' % len(ot))

print()
print('=== TASKS ===')
td = obj['taskData']
print('NewTaskData: %d items; tasks: %d; changeCount: %s' % (
    len(td['NewTaskData']), len(td['tasks']), td.get('changeCount')))
print('NewTaskData[0]:', json.dumps(td['NewTaskData'][0], ensure_ascii=False)[:200])

print()
print('=== SETTINGS / PLATFORM ===')
se = obj['settingData']
print('UseRijData:', se.get('UseRijData'), ' UseNewtonJsonData:', se.get('UseNewtonJsonData'), ' ChillyUid:', se.get('ChillyUid'))
print('platformId:', obj.get('platformId'))
print('cloudSaveId:', obj.get('cloudSaveId'))
print('serialzedVersion:', obj.get('serialzedVersion'))

print()
print('=== NULL SECTIONS (absent features) ===')
for k in ['sandboxConfig', 'mallReloadData', 'monsterRiseData', 'weaponEvolutionData', 'miscData', 'pvpData', 'battlePassSaveData', 'escapeSeasonData']:
    print('  %-24s %r' % (k, obj.get(k)))
