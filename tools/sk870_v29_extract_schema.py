#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract_save_schema.py — полная схема сохранений Soul Knight 8.7.0
из расшифрованных IL2CPP-метаданных (v29).

Строит download/sk870_save_schema.json:
  - корневые классы файлов локальных сейвов (map: файл -> класс -> поля)
  - все вложенные классы, встречающиеся в схеме (по имени, 1 уровень вложенности по соглашению имён)
  - литералы-факты (файлы, ключи, prefs-имена)

Запуск:
  python3 extract_save_schema.py /tmp/global-metadata.dec.dat ../download/sk870_save_schema.json
"""
import json
import re
import struct
import sys

# корневые классы: файл -> класс
FILE_CLASS_MAP = {
    'game.data': 'AboGameData',
    'backup.data': 'BackupGameData',
    'statistic.data': 'StatisticData',
    'item_data.data': 'ItemData',
    'misc_data.data': 'MiscData',
    'pvp_data.data': 'PvpData',
    'season_data.data': 'SeasonData',
    'weapon_evolution_data.data': 'WeaponEvolutionData',
    'mall_reload_data.data': 'MallReloadData',
    'escape_season_data.data': 'EscapeSeasonData',
    'escape_run_save.data': 'EscapeRunSaveData',
    'bp_data.data': 'BattlePassSaveData',
    'task.data': 'TaskData',
    'setting.data': 'SettingData',
    'sandbox_config.data': 'SandboxConfigData',
    'sandbox_maps.data': 'SandboxMaps',
    'monsrise_data.data': 'MonsterRiseData',
    'recordSave.data': 'RecordData',
    'battles.data': 'BATTLES_RAW_JSON',
    'battle.data': 'BATTLES_RAW_JSON',
}


class Meta:
    def __init__(self, path):
        dec = open(path, 'rb').read()
        self.dec = dec
        pairs = []
        for i in range(31):
            off, sz = struct.unpack_from('<II', dec, 8 + i * 8)
            pairs.append((off, sz))
        self.pairs = pairs
        sOff, sSz = pairs[2]
        self.sOff, self.sSz = sOff, sSz
        self.name_pos = {}
        pos = 0
        for chunk in dec[sOff:sOff + sSz].split(b'\x00'):
            if chunk:
                try:
                    self.name_pos.setdefault(chunk.decode('utf-8'), pos)
                except UnicodeDecodeError:
                    pass
            pos += len(chunk) + 1
        self.tOff = pairs[19][0]
        self.nTypes = pairs[19][1] // 88
        self.fOff = pairs[11][0]
        self.nFields = pairs[11][1] // 12

    def s(self, idx):
        if idx is None or idx < 0 or idx >= self.sSz:
            return None
        o = self.sOff + idx
        try:
            end = self.dec.index(b'\x00', o)
        except ValueError:
            return None
        return self.dec[o:end].decode('utf-8', 'replace')

    def type_by_name(self, name):
        p = self.name_pos.get(name)
        if p is None:
            return None
        for i in range(self.nTypes):
            base = self.tOff + i * 88
            if struct.unpack_from('<I', self.dec, base)[0] == p:
                return (i, base)
        return None

    def fields(self, base):
        """[(имя, это_статик, это_событие)] — эвристика по flags поля не даёт
        статичность без Il2CppType, поэтому помечаем известные события отдельно."""
        fs = struct.unpack_from('<i', self.dec, base + 32)[0]
        fc = struct.unpack_from('<H', self.dec, base + 68)[0]
        out = []
        if fs < 0 or fc <= 0:
            return out
        for k in range(fc):
            fi = fs + k
            if fi >= self.nFields:
                break
            nidx = struct.unpack_from('<I', self.dec, self.fOff + fi * 12)[0]
            out.append(self.s(nidx))
        return out


def main():
    dec_path, out_path = sys.argv[1], sys.argv[2]
    m = Meta(dec_path)
    schema = {'meta': {
        'game': 'Soul Knight 8.7.0 (com.ChillyRoom.DungeonShooter)',
        'source': 'IL2CPP global-metadata.dat v29, расшифровка XOR 0x66 по зонам',
        'fieldCount_offset': 68,
        'confirmed_on_real_files': ['statistic.data == StatisticData (161/161 ключей)'],
    }, 'file_class_map': {}, 'classes': {}, 'crypto': {
        'des': {'statistic': 'key crst1\\0\\0\\0, IV Ahbool\\0\\0, CBC, PKCS5, Base64 NO_WRAP',
                'default': 'key iambo\\0\\0\\0, IV Ahbool\\0\\0, CBC, PKCS5, Base64 NO_WRAP'},
        'xor_game_data': 'ключ 15 байт slcz}guc\\x7fWmlkJ_ (data[i]^=key[i%15])',
        'rijndael_migrated': ['monsrise_data', 'item_data', 'pvp_data', 'misc_data',
                              'task', 'bp_data', 'mall_reload_data'],
        'rijndael_keys_in_prefs': ['DataRijndael128Key', 'DataRijndaelIV'],
        'rij_test_prefs': ['OpenRijTest_{accountId}', 'RijUserAutoOpenJsonTestIfSupport'],
        'file_ext': '.rij',
        'pipeline': 'Abo.JsonUtil.SaveJsonWithCrypt / LoadJsonWithCrypt, Abo.CryptUtil',
    }, 'literals': {
        'save_files': ['/game.data', '/backup.data', '/statistic.data', '/item_data.data',
                       '/misc_data.data', '/pvp_data.data', '/season_data.data',
                       '/weapon_evolution_data.data', '/mall_reload_data.data',
                       '/escape_season_data.data', '/escape_run_save.data', '/bp_data.data',
                       '/task.data', '/setting.data', '/sandbox_config.data',
                       '/sandbox_maps.data', '/monsrise_data.data', '/recordSave.data',
                       '/battles.data', '/battle.data'],
        'des_keys': ['crst1', 'iambo', 'Ahbool', 'abo123', 'crlc1', 'PASSWORD'],
    }}

    def add_class(cls_name):
        if cls_name in schema['classes'] or cls_name.startswith('BATTLES'):
            return
        hit = m.type_by_name(cls_name)
        if hit is None:
            schema['classes'][cls_name] = {'error': 'typeDef not found'}
            return
        i, base = hit
        fs = struct.unpack_from('<i', m.dec, base + 32)[0]
        ms = struct.unpack_from('<i', m.dec, base + 36)[0]
        fc = struct.unpack_from('<H', m.dec, base + 68)[0]
        mc = struct.unpack_from('<H', m.dec, base + 66)[0]
        ns = m.s(struct.unpack_from('<I', m.dec, base + 4)[0]) or ''
        schema['classes'][cls_name] = {
            'typeDefIndex': i, 'namespace': ns,
            'fieldStart': fs, 'fieldCount': fc,
            'fields': m.fields(base),
            'methodCount': mc,
        }

    for fname, cls in FILE_CLASS_MAP.items():
        schema['file_class_map'][fname] = cls
        add_class(cls)

    # вложенные: CloudSaveGameData секции -> одноимённые классы уже в карте;
    # добавим известные вложенные структуры статистики/валют
    for extra in ['AboGameData', 'CloudSaveGameData', 'StatisticData',
                  'MonsterRiseData', 'MonsterRisePVEData', 'MonsterRisePVPTrackData',
                  'RecordData', 'RecordDataList', 'RecordDataDic', 'RecordDataDicInt',
                  'RecordDataDicFloat', 'RecordDataContainer']:
        add_class(extra)

    json.dump(schema, open(out_path, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print('[ok] schema -> %s' % out_path)
    for f, c in FILE_CLASS_MAP.items():
        fields = schema['classes'].get(c, {}).get('fields')
        print('  %-28s %-22s %s полей' % (f, c, len(fields) if fields else '?'))


if __name__ == '__main__':
    main()
