/*
 * frida_sk_snapshot_dump.js  v3.2  (2026-11-11)
 * (runs in the GAME process - Soul Knight 8.7.0, legacy PGS v1 SDK)
 *
 * v3.1 -> v3.2 (readSnap failed with java.lang.IllegalStateException:
 *   "Must provide a valid conflict policy!"):
 *   DIAGNOSIS: the overload pick was CORRECT ('pointer, uint8, int32' ==
 *   open(String, boolean, int)) and RESOLUTION_POLICY_MANUAL(0) WAS passed in -
 *   i.e. this server/client combination REJECTS policy 0 (MANUAL).
 *   FIX: readSnap runs a policy TRY-CHAIN: open(name, false, POLICY) is
 *   attempted with MOST_RECENTLY_MODIFIED(3) -> LONGEST_PLAYTIME(1) ->
 *   LAST_KNOWN_GOOD(2) -> HIGHEST_PROGRESS(4) -> MANUAL(0) (values read from
 *   the SDK class constants); first success wins and is reported. With exactly
 *   one metadata entry per uniqueName (pre-checked via load()) an auto-policy
 *   open is a pure read - nothing is resolved/overwritten. If every String-open
 *   fails, the open(SnapshotMetadata[, policy]) overloads are tried too.
 *   Plus: getSnapshotContents()/getContents() fallback; readFully() ->
 *   readFully(byte[]) -> dup(ParcelFileDescriptor) copy chain; save fallback
 *   dirs (/sdcard/skdump -> app external -> app internal); sha256 + first
 *   32 bytes hex printed on every dump; conflict dump handles both
 *   SnapshotConflict shapes; passiveOn() now also dumps LIVE Snapshot /
 *   SnapshotContents objects found in the heap (the game's own open() becomes
 *   an alternate read path); blockWritesOn() additionally hooks
 *   resolve()/deleteSnapshot().
 *
 * v3 -> v3.1 (after first successful listNow()):
 *   (1) open() overload match FIXED: this Frida bridge reports object params
 *       as 'pointer' and boolean as 'uint8' -> open(String,boolean,int) is
 *       'pointer, uint8, int32', not 'java.lang.String, boolean, int';
 *   (2) Tasks.await added for the open() Task (v3 forgot it - open returns
 *       a Task, not DataOrConflict);
 *   (3) Owner drilled to player displayName/playerId (was [object Object]);
 *   (4) ActivityThread activity-finder removed: currentActivityThread() is
 *       thread-local and returns null on the Frida JS thread -> TypeError;
 *       Java.choose works fine;
 *   (5) NEW blockWritesOn()/blockWritesOff(): cloud write/commit blocker for
 *       the FROZEN-account session. The game dual-writes its local save into
 *       GPGS every session - with the frozen account on the emulator that
 *       would CLOBBER GPV2_* / SoulKnightCloud with the emulator's local state.
 *       Stubs never call the original -> the v2 recursion pattern is absent.
 *
 * ============ v2 -> v3: WHY ALL HOOKS WERE REMOVED ============
 * v2 crash post-mortem (tombstone, 512 frames):
 *   every cycle = frida-agent glue -> art_quick_generic_jni_trampoline ->
 *   ArtMethod::Invoke -> JNI::CallNonvirtualObjectMethod -> frida glue ...
 *   i.e. "call original from inside implementation" (this.get(idx) /
 *   this.getContents() / this.loadClass()) re-entered the REPLACEMENT
 *   instead of the original on Frida 17.6.2 + Android 14 x86_64 ->
 *   infinite recursion -> stack overflow -> SIGSEGV (binder thread).
 *   The ClassLoader.loadClass hook (fires on EVERY class load, calls
 *   Java.use() inside) made it worse.
 * v3 policy: ZERO Java implementation hooks. Nothing can recurse.
 *   Everything is driven actively from the REPL; "passive" mode polls
 *   the heap with Java.choose (safe, no hooks).
 * ==============================================================
 *
 * Commands (REPL):
 *   listNow()                    - enumerate ALL GPGS snapshots of the signed-in
 *                                  account (metadata only, read-only).
 *   readSnap('<uniqueName>')     - download snapshot CONTENTS via the game's own
 *                                  PGS v1 SDK (open with createIfMissing=FALSE;
 *                                  conflict policy is auto-tried, see changelog)
 *                                  and write bytes to /sdcard/skdump/snap_<uniqueName>_<ts>.bin
 *   passiveOn(3) / passiveOff()  - poll heap for live SnapshotMetadataBuffers
 *                                  AND live Snapshot/SnapshotContents objects
 *                                  (catches what the game loads on its own;
 *                                  content is dumped once, deduped by name@size)
 *   status()                     - which SDK classes are present
 *   blockWritesOn()              - BLOCK all cloud write/commit entrypoints
 *                                  (arm BEFORE letting the frozen account sign in;
 *                                  reads listNow/readSnap are NOT affected)
 *
 * After capture:
 *   adb pull /sdcard/skdump C:\Users\<you>\skdump
 *   python sk870_decrypt_all.py skdump\snap_*.bin
 *
 * USAGE:
 *   1) START the game first (v1 crashed it; process must be fresh),
 *      wait for the main menu so GPGS auto sign-in completes.
 *   2) frida -U -n "Soul Knight" -l frida_sk_snapshot_dump.js
 *   3) listNow()  ->  readSnap('SoulKnightCloud')
 */

'use strict';

var SMB_V1    = 'com.google.android.gms.games.snapshot.SnapshotMetadataBuffer'; // legacy v1
var SMB_V2    = 'com.google.android.gms.games.snapshots.SnapshotMetadataBuffer'; // PGS v2
var SC_LEGACY = 'com.google.android.gms.games.SnapshotsClient';                  // Tasks-based v1 client
var OUT_DIR   = '/sdcard/skdump';

function log(s) { console.log(s); }
function jstr(x) { try { return '' + x; } catch (e) { return '<unprintable>'; } }

function safe(what, fn) {
  try { return fn(); } catch (e) { log('    [!] ' + what + ': ' + e); return undefined; }
}

/* ---------- helpers ---------- */

// Objects returned by generic methods (Tasks.await etc.) are bridged as
// java.lang.Object and their methods are unresolvable -> retype via runtime class.
function retyped(obj) {
  if (obj === null || obj === undefined) return obj;
  var name = null;
  try { name = obj.getClass().getName(); } catch (e) { return obj; }
  if (name === null || name === 'java.lang.Object') return obj;
  try { return Java.cast(obj, Java.use(name)); } catch (e) { return obj; }
}

// Unwrap AnnotatedData<T> and similar single-get() wrappers.
function unwrapOne(obj) {
  var hops = 0;
  while (obj !== null && obj !== undefined && hops < 3) {
    var name = null;
    try { name = obj.getClass().getName(); } catch (e) { break; }
    if (name === null) break;
    if (name.indexOf('AnnotatedData') !== -1) {
      var next = null;
      try { next = obj.get(); } catch (e) { log('    [!] AnnotatedData.get(): ' + e); break; }
      obj = retyped(next);
      hops++;
      continue;
    }
    break;
  }
  return obj;
}

function findActivity() {
  // NOTE: ActivityThread.currentActivityThread() is thread-local and returns
  // null on the Frida JS thread (that was the v3 'TypeError: not a function').
  // Java.choose works reliably - use it directly.
  var chosen = null;
  Java.choose('android.app.Activity', {
    onMatch: function (a) { if (chosen === null) chosen = a; },
    onComplete: function () {}
  });
  return chosen;
}

function makeSnapshotsClient(act) {
  var client = safe('PlayGames.getSnapshotsClient', function () {
    var PG = Java.use('com.google.android.gms.games.PlayGames');
    var c = PG.getSnapshotsClient(act);
    log('[+] client: PlayGames.getSnapshotsClient(activity)');
    return c;
  });
  if (client !== null && client !== undefined) return client;

  client = safe('new SnapshotsClient(activity)', function () {
    var SC = Java.use(SC_LEGACY);
    var c = SC.$new(act);
    log('[+] client: new ' + SC_LEGACY + '(activity)');
    return c;
  });
  return client;
}

/* ---------- metadata dump ---------- */

// Java objects come back as wrappers ([object Object]); drill into known shapes.
function smartStr(v) {
  if (v === null || v === undefined) return undefined;
  if (typeof v !== 'object') return jstr(v);
  var o = retyped(v);
  var id = null, name = null;
  try { id = jstr(o.getPlayerId()); } catch (e) {}
  try { name = jstr(o.getDisplayName()); } catch (e) {}
  if (id === 'null' || id === 'undefined') id = null;
  if (name === 'null' || name === 'undefined') name = null;
  if (id !== null || name !== null) {
    return 'player "' + (name || '?') + '" (#' + (id || '?') + ')';
  }
  try {
    var s = o.toString();
    if (typeof s === 'object' && s !== null) { s = retyped(s); s = jstr(s); }
    if (typeof s === 'string' && s.indexOf('[object Object]') !== 0) return s;
  } catch (e) {}
  return jstr(v);
}

var META_GETTERS = [
  'getUniqueName', 'getSnapshotId', 'getTitle', 'getDescription',
  'getFileName', 'getDeviceName', 'getOwner',
  'getLastModifiedTimestamp', 'getPlayedTime', 'getProgressValue'
];

function dumpMeta(meta, idx) {
  if (meta === null || meta === undefined) { log('  [' + idx + '] <null>'); return; }
  var cls = '?';
  try { cls = meta.getClass().getName(); } catch (e) {}
  log('  [' + idx + '] ' + cls);
  for (var i = 0; i < META_GETTERS.length; i++) {
    var g = META_GETTERS[i];
    try {
      var v = meta[g]();
      if (v === undefined) continue;
      var line = '      ' + g.substring(3) + ' = ' + smartStr(v);
      if (g === 'getLastModifiedTimestamp') {
        var s = jstr(v);
        if (/^\d{12,}$/.test(s)) {
          try { line += '  (' + new Date(parseInt(s, 10)).toISOString() + ')'; } catch (e) {}
        }
      }
      log(line);
    } catch (e) { /* getter absent in this SDK version - skip silently */ }
  }
  try { log('      toString = ' + smartStr(meta.toString())); } catch (e) {}
}

function dumpBuffer(buf, label) {
  var n = 0;
  try { n = buf.getCount(); } catch (e) { log('[!] getCount(): ' + e); return 0; }
  log('[+] ' + label + ': ' + n + ' snapshot(s)');
  for (var i = 0; i < n; i++) {
    try { dumpMeta(buf.get(i), i); } catch (e) { log('  [' + i + '] ' + e); }
  }
  return n;
}

/* ---------- listNow ---------- */

function listNow() {
  return Java.perform(function () {
    log('[*] listNow: probing SDK classes ...');
    [SMB_V2, SMB_V1, SC_LEGACY,
     'com.google.android.gms.tasks.Tasks',
     'com.google.android.gms.common.data.DataHolder'].forEach(function (cn) {
      try { Java.use(cn); log('    [+] ' + cn); } catch (e) { log('    [-] ' + cn); }
    });

    var act = findActivity();
    if (act === null || act === undefined) {
      log('[!] no live Activity. Launch Soul Knight first.');
      return 'no-activity';
    }

    var client = makeSnapshotsClient(act);
    if (client === null || client === undefined) {
      log('[!] cannot build SnapshotsClient');
      return 'no-client';
    }

    var task = safe('client.load(true)', function () { return client.load(true); });
    if (task === null || task === undefined) {
      safe('load.overloads', function () {
        var ovs = Java.use(SC_LEGACY).load.overloads;
        ovs.forEach(function (o) {
          log('    load(' + o.argumentTypes.map(function (t) { return t.type; }).join(', ') + ')');
        });
      });
      return 'no-load-task';
    }

    log('[*] awaiting snapshot list (30s timeout) ...');
    var Tasks = Java.use('com.google.android.gms.tasks.Tasks');
    var result = safe('Tasks.await', function () {
      var TimeUnit = Java.use('java.util.concurrent.TimeUnit');
      try { return Tasks.await(task, 30, TimeUnit.SECONDS.value); }
      catch (eFirst) {
        log('    [i] timed/failed await (' + eFirst + '), retrying without timeout ...');
        return Tasks.await(task);
      }
    });
    if (result === null || result === undefined) {
      safe('task.getException', function () {
        var ex = task.getException();
        if (ex === null || ex === undefined) { log('    task finished with no result and no exception'); return; }
        log('    task failed: ' + ex.getClass().getName() + ': ' + jstr(ex.getMessage()));
        try { log('    status code = ' + ex.getStatusCode()); } catch (e) {}
      });
      return 'load-failed';
    }

    var bufObj = unwrapOne(retyped(result));
    var buf = null;
    var castErr = null;
    try { buf = Java.cast(bufObj, Java.use(SMB_V1)); }
    catch (eV1) {
      try { buf = Java.cast(bufObj, Java.use(SMB_V2)); }
      catch (eV2) { castErr = eV1 + ' / ' + eV2; }
    }
    if (buf === null || buf === undefined) {
      log('[!] result is not a SnapshotMetadataBuffer: ' + castErr);
      return 'not-a-buffer';
    }

    var n = dumpBuffer(buf, 'GPGS snapshots of the signed-in account');
    log('[*] done. Next: readSnap(\'<uniqueName>\') - read-only content dump to ' + OUT_DIR);
    return n + ' snapshot(s)';
  });
}

/* ---------- readSnap: content download, read-only ---------- */

// v3.2: MANUAL(0) is rejected by this server/client combination (see header),
// so we auto-try every policy; first success wins.
var POLICY_ORDER = [
  'RESOLUTION_POLICY_MOST_RECENTLY_MODIFIED',
  'RESOLUTION_POLICY_LONGEST_PLAYTIME',
  'RESOLUTION_POLICY_LAST_KNOWN_GOOD',
  'RESOLUTION_POLICY_HIGHEST_PROGRESS',
  'RESOLUTION_POLICY_MANUAL'
];
var POLICY_FALLBACK_VALUES = { // used only if the SDK class lacks the constant
  RESOLUTION_POLICY_MOST_RECENTLY_MODIFIED: 3,
  RESOLUTION_POLICY_LONGEST_PLAYTIME: 1,
  RESOLUTION_POLICY_LAST_KNOWN_GOOD: 2,
  RESOLUTION_POLICY_HIGHEST_PROGRESS: 4,
  RESOLUTION_POLICY_MANUAL: 0
};

function sha256(bytes) {
  var d = Java.use('java.security.MessageDigest').getInstance('SHA-256').digest(bytes);
  var out = '';
  for (var i = 0; i < d.length; i++) {
    var v = d[i] & 0xff;
    out += (v < 16 ? '0' : '') + v.toString(16);
  }
  return out;
}

function headHex(bytes, n) {
  var out = [];
  for (var i = 0; i < n && i < bytes.length; i++) {
    var v = bytes[i] & 0xff;
    out.push((v < 16 ? '0' : '') + v.toString(16));
  }
  return out.join(' ');
}

function awaitTask(task, sec, what) {
  var Tasks = Java.use('com.google.android.gms.tasks.Tasks');
  var TimeUnit = Java.use('java.util.concurrent.TimeUnit');
  try {
    return Tasks.await(task, sec, TimeUnit.SECONDS.value);
  } catch (e) {
    var msg = '' + e;
    if (msg.indexOf('timed out') >= 0 || msg.indexOf('TimeoutException') >= 0) {
      log('    [i] ' + what + ': timed out - awaiting without timeout ...');
      return Tasks.await(task);
    }
    throw e;
  }
}

function loadMetaBuffer(client) {
  var task = client.load(true); // same call listNow() uses successfully
  var result = awaitTask(task, 30, 'load');
  var bufObj = unwrapOne(retyped(result));
  try { return Java.cast(bufObj, Java.use(SMB_V1)); }
  catch (eV1) { return Java.cast(bufObj, Java.use(SMB_V2)); }
}

function findMetaByName(client, uniqueName) {
  var buf = loadMetaBuffer(client);
  var n = buf.getCount();
  var found = null, dup = 0;
  for (var i = 0; i < n; i++) {
    var m = buf.get(i);
    var nm = null;
    try { nm = '' + m.getUniqueName(); } catch (e) {}
    if (nm === uniqueName) { if (found === null) found = m; dup++; }
  }
  if (dup > 1) log('[!] ' + dup + ' metadata entries share uniqueName "' + uniqueName + '" - a conflict is likely');
  return found;
}

function getSnapshotContentsAny(snap) {
  var c = safe('getSnapshotContents()', function () { return snap.getSnapshotContents(); });
  if (c === null || c === undefined) {
    c = safe('getContents()', function () { return snap.getContents(); });
  }
  return c;
}

function readBytesAny(contents) {
  var bytes = safe('readFully()', function () { return contents.readFully(); });
  if (bytes !== null && bytes !== undefined) return bytes;

  bytes = safe('readFully(byte[]) via PFD size', function () {
    var pfd = contents.getParcelFileDescriptor();
    var size = pfd.getStatSize();
    log('    [i] ParcelFileDescriptor.statSize = ' + size);
    var dst = Java.array('byte', new Array(Math.max(size, 1)).fill(0));
    var okB = contents.readFully(dst);
    if (okB === false) { log('    [!] readFully(byte[]) returned false'); return null; }
    return dst;
  });
  if (bytes !== null && bytes !== undefined) return bytes;

  return safe('copy via dup() of ParcelFileDescriptor', function () {
    var pfd = contents.getParcelFileDescriptor();
    var dup = pfd.dup();
    var fis = Java.use('java.io.FileInputStream').$new(dup.getFileDescriptor());
    var baos = Java.use('java.io.ByteArrayOutputStream').$new();
    var buf = Java.array('byte', new Array(65536).fill(0));
    var total = 0;
    while (true) {
      var n = fis.read(buf);
      if (n < 0) break;
      if (n > 0) { baos.write(buf, 0, n); total += n; }
    }
    fis.close();
    try { dup.close(); } catch (e) {}
    log('    [i] PFD copy = ' + total + ' bytes');
    return baos.toByteArray();
  });
}

function dumpConflictAny(conflict, uniqueName) {
  log('[!] CONFLICT on "' + uniqueName + '" - dumping every side');
  var done = safe('getConflictingSnapshots()', function () {
    var list = conflict.getConflictingSnapshots();
    var n = list.size();
    log('    conflicting versions: ' + n);
    for (var i = 0; i < n; i++) {
      (function (i) {
        var s = retyped(list.get(i));
        try { dumpMeta(retyped(s.getMetadata()), 'conflict-' + i); } catch (e) {}
        var c = getSnapshotContentsAny(s);
        if (c !== null && c !== undefined) {
          var b = readBytesAny(c);
          if (b !== null && b !== undefined) {
            var p = saveBytes('conflict_' + i + '_' + uniqueName + '_' + Date.now() + '.bin', b);
            log('    -> conflict ' + i + ': ' + b.length + ' bytes -> ' + p);
            log('       sha256 = ' + sha256(b));
          }
        }
      })(i);
    }
    return true;
  });
  if (done) return;
  safe('legacy conflict getters', function () {
    var a = retyped(conflict.getConflictingSnapshotMetadata());
    var b = retyped(conflict.getResolutionSnapshotMetadata());
    log('  -- conflicting side:'); dumpMeta(a, 'C');
    log('  -- resolution side:');  dumpMeta(b, 'R');
    var cc = retyped(conflict.getConflictingSnapshotContents());
    if (cc !== null && cc !== undefined) {
      var bb = readBytesAny(cc);
      if (bb !== null && bb !== undefined) {
        var p = saveBytes('conflict_' + uniqueName + '_' + Date.now() + '.bin', bb);
        log('  conflicting content: ' + bb.length + ' bytes -> ' + p + '  sha256=' + sha256(bb));
      }
    }
  });
}

function saveBytes(name, bytes) {
  var File = Java.use('java.io.File');
  var dirs = [File.$new(OUT_DIR)];
  safe('fallback dirs', function () {
    var act = findActivity();
    if (act) {
      var efd = act.getExternalFilesDir(null);
      if (efd !== null && efd !== undefined) dirs.push(File.$new('' + efd.getAbsolutePath() + '/skdump'));
      var ifd = act.getFilesDir();
      if (ifd !== null && ifd !== undefined) dirs.push(File.$new('' + ifd.getAbsolutePath() + '/skdump'));
    }
  });
  for (var i = 0; i < dirs.length; i++) {
    try {
      var d = dirs[i];
      if (!d.exists()) d.mkdirs();
      var f = File.$new(d, name);
      var fos = Java.use('java.io.FileOutputStream').$new(f);
      fos.write(bytes);
      fos.flush();
      fos.close();
      if (i > 0) log('    [i] primary ' + OUT_DIR + ' not writable - used fallback: ' + d.getAbsolutePath());
      return '' + f.getAbsolutePath();
    } catch (e) {
      log('    [!] write to ' + dirs[i].getAbsolutePath() + ': ' + e);
    }
  }
  return null;
}

function readSnap(uniqueName) {
  return Java.perform(function () {
    if (uniqueName === null || uniqueName === undefined || '' + uniqueName === '') {
      log('[!] usage: readSnap("<uniqueName>") e.g. readSnap("SoulKnightCloud")');
      return 'bad-args';
    }
    uniqueName = '' + uniqueName;

    var act = findActivity();
    if (act === null || act === undefined) { log('[!] no live Activity'); return 'no-activity'; }
    var client = makeSnapshotsClient(act);
    if (client === null || client === undefined) return 'no-client';

    var SC = Java.use(SC_LEGACY);

    // ---- conflict policies available in this SDK build (constants first) ----
    var policies = [];
    POLICY_ORDER.forEach(function (pn) {
      var v;
      try { v = SC[pn].value; } catch (e) { v = POLICY_FALLBACK_VALUES[pn]; }
      policies.push({ name: pn, value: v });
    });
    log('[i] policy chain: ' + policies.map(function (p) { return p.name.split('RESOLUTION_POLICY_')[1] + '(' + p.value + ')'; }).join(' -> '));

    // pick open(String, boolean, int)
    // NOTE: on this Frida bridge object params report as 'pointer', boolean as
    // 'uint8', int as 'int32' -> open(String, boolean, int) = 'pointer, uint8, int32'
    var chosen = null, sigs = [];
    SC.open.overloads.forEach(function (o) {
      var sig = o.argumentTypes.map(function (t) { return t.type; }).join(', ');
      sigs.push(sig);
      if (sig === 'pointer, uint8, int32' || sig === 'java.lang.String, boolean, int') chosen = o;
    });
    if (chosen === null) {
      // loose fallback: 3 params, middle boolean-ish, last int-ish
      var canon = function (t) {
        if (t === 'boolean' || t === 'uint8') return 'boolean';
        if (t === 'int' || t === 'int32') return 'int';
        return t;
      };
      SC.open.overloads.forEach(function (o) {
        if (chosen !== null) return;
        var ts = o.argumentTypes.map(function (t) { return canon('' + t.type); });
        if (ts.length === 3 && ts[1] === 'boolean' && ts[2] === 'int') chosen = o;
      });
    }
    log('[i] open overloads: ' + (sigs.join(' | ') || '<none>'));
    if (chosen === null) {
      log('[!] open(String, boolean, int) not found - paste the overload list above back to me');
      return 'no-suitable-open';
    }

    // ---- pre-check: how many metadata entries share this uniqueName? ----
    var mdFound = null;
    safe('metadata pre-check', function () {
      mdFound = findMetaByName(client, uniqueName);
      if (mdFound === null || mdFound === undefined) log('[!] no metadata entry for "' + uniqueName + '" (yet) - open() may fail with "not found"');
      return true;
    });

    // ---- attempt 1: open(String, false, policy) for every policy ----
    // createIfMissing = FALSE -> strictly read-only, cannot create/overwrite
    var doc = null, usedPolicy = null;
    for (var i = 0; i < policies.length && (doc === null || doc === undefined); i++) {
      var p = policies[i];
      log('[i] trying open(String, false, ' + p.name.split('RESOLUTION_POLICY_')[1] + '=' + p.value + ') ...');
      doc = safe('open ' + p.name, function () {
        var task = chosen.call(client, uniqueName, false, p.value);
        var d = awaitTask(task, 30, 'open');
        log('    [+] open OK with ' + p.name);
        return retyped(d);
      });
      if (doc !== null && doc !== undefined) usedPolicy = p.name;
    }

    // ---- attempt 2: open(SnapshotMetadata[, policy]) ----
    if (doc === null || doc === undefined) {
      log('[i] every String-open failed - trying open(SnapshotMetadata, policy) ...');
      if (mdFound === null || mdFound === undefined) {
        mdFound = safe('metadata retry', function () { return findMetaByName(client, uniqueName); });
      }
      if (mdFound !== null && mdFound !== undefined) {
        var mdInt = null, md1 = null;
        SC.open.overloads.forEach(function (o) {
          var ts = o.argumentTypes.map(function (t) { return '' + t.type; });
          if (ts.length === 2 && (ts[1] === 'int' || ts[1] === 'int32')) mdInt = o;
          if (ts.length === 1) md1 = o;
        });
        if (mdInt !== null) {
          for (var j = 0; j < policies.length && (doc === null || doc === undefined); j++) {
            var pp = policies[j];
            log('[i] trying open(SnapshotMetadata, ' + pp.name.split('RESOLUTION_POLICY_')[1] + '=' + pp.value + ') ...');
            doc = safe('open(md) ' + pp.name, function () {
              var task = mdInt.call(client, mdFound, pp.value);
              var d = awaitTask(task, 30, 'open(md)');
              log('    [+] open(md) OK with ' + pp.name);
              return retyped(d);
            });
            if (doc !== null && doc !== undefined) usedPolicy = pp.name + ' (md)';
          }
        }
        if ((doc === null || doc === undefined) && md1 !== null) {
          log('[i] trying open(SnapshotMetadata) ...');
          doc = safe('open(md)', function () {
            var task = md1.call(client, mdFound);
            var d = awaitTask(task, 30, 'open(md)');
            log('    [+] open(SnapshotMetadata) OK');
            return retyped(d);
          });
          if (doc !== null && doc !== undefined) usedPolicy = 'default (md)';
        }
      } else {
        log('[!] metadata not found - cannot use the SnapshotMetadata open path');
      }
    }

    if (doc === null || doc === undefined) {
      log('[!] EVERY open() variant failed. Paste this log back to me.');
      log('[i] overloads were: ' + sigs.join(' | '));
      return 'open-failed';
    }
    log('[+] opened "' + uniqueName + '" (policy=' + usedPolicy + ')');

    // ---- conflict? ----
    var isConflict = safe('isConflict()', function () { return doc.isConflict(); });
    if (isConflict === true) {
      var c = retyped(safe('getConflict()', function () { return doc.getConflict(); }));
      if (c !== null && c !== undefined) dumpConflictAny(c, uniqueName);
      return 'conflict';
    }

    // ---- read content ----
    var snap = retyped(safe('getData()', function () { return doc.getData(); }));
    if (snap === null || snap === undefined) { log('[!] DataOrConflict.getData() = null'); return 'no-data'; }

    var meta = null;
    safe('snapshot accessors', function () { meta = snap.getMetadata(); });
    if (meta !== null && meta !== undefined) { log('[i] metadata:'); dumpMeta(retyped(meta), 0); }

    var contents = getSnapshotContentsAny(snap);
    if (contents === null || contents === undefined) { log('[!] no SnapshotContents'); return 'no-contents'; }

    var bytes = readBytesAny(contents);
    if (bytes === null || bytes === undefined) { log('[!] cannot read snapshot content'); return 'empty'; }

    var path = saveBytes('snap_' + uniqueName + '_' + Date.now() + '.bin', bytes);
    if (path === null) { log('[!] save FAILED - content only in memory'); return 'save-failed'; }
    log('[+] saved: ' + path + ' (' + bytes.length + ' bytes)');
    log('[+] sha256   = ' + sha256(bytes));
    log('[+] head(32) = ' + headHex(bytes, 32));
    safe('contents.close()', function () { contents.close(); });
    log('[i] adb pull "' + path + '"   then: python sk870_decrypt_all.py <file>');
    return path;
  });
}

/* ---------- cloud-write blocker (for the FROZEN-account session) ----------
 * The game dual-writes its local save into GPGS on EVERY session. With the
 * frozen account logged into the emulator that would clobber the account's
 * real cloud snapshots (GPV2_*, SoulKnightCloud) with the emulator's local
 * state. These stubs NEVER call the original (the v2 crash pattern was
 * "call original from replacement" - absent here, so no recursion).
 * Reads (listNow / readFully / getContents) are NOT touched.
 */

var blockedOverloads = [];

function blockWritesOn() {
  return Java.perform(function () {
    var Tasks = Java.use('com.google.android.gms.tasks.Tasks');
    var n = 0;

    // 1) snapshot CONTENT writes -> always fail (return false)
    ['com.google.android.gms.games.snapshot.SnapshotContents',
     'com.google.android.gms.games.snapshot.SnapshotContentsEntity'].forEach(function (cn) {
      var C = null;
      try { C = Java.use(cn); } catch (e) { return; }
      var names = {};
      try {
        var ms = C.class.getDeclaredMethods();
        for (var i = 0; i < ms.length; i++) {
          var mn = jstr(ms[i].getName());
          if (mn.indexOf('write') === 0) names[mn] = true;
        }
      } catch (e) {}
      Object.keys(names).forEach(function (mn) {
        try {
          C[mn].overloads.forEach(function (o) {
            blockedOverloads.push(o);
            o.implementation = function () {
              log('[BLOCK] ' + cn.substring(cn.lastIndexOf('.') + 1) + '.' + mn + ' -> DENIED');
              return false;
            };
          });
          log('[+] write-block armed: ' + cn + '.' + mn);
          n++;
        } catch (e) { log('[!] arm ' + cn + '.' + mn + ': ' + e); }
      });
    });

    // 2) commit/resolve/delete calls -> failed Task (graceful for the game)
    ['commitAndClose', 'commit', 'commitAndCloseWithMetadataChange',
     'resolve', 'deleteSnapshot'].forEach(function (mn) {
      try {
        var m = Java.use(SC_LEGACY)[mn];
        if (!m || !m.overloads) return;
        m.overloads.forEach(function (o) {
          blockedOverloads.push(o);
          o.implementation = function () {
            log('[BLOCK] SnapshotsClient.' + mn + ' -> DENIED (failed Task)');
            try {
              return Tasks.forException(Java.use('java.io.IOException').$new('sk_dump: cloud writes blocked'));
            } catch (e) { return null; }
          };
        });
        log('[+] commit-block armed: SnapshotsClient.' + mn);
        n++;
      } catch (e) {}
    });

    if (n === 0) log('[!] NOTHING armed - paste this log back to me');
    else log('[+] cloud writes BLOCKED (' + n + ' entrypoints). Reads are NOT touched.');
    return 'writes-blocked';
  });
}

function blockWritesOff() {
  if (blockedOverloads.length === 0) { log('[i] writes are not blocked'); return 'not-blocked'; }
  var n = 0;
  blockedOverloads.forEach(function (o) {
    try { o.implementation = null; n++; } catch (e) { log('    [!] revert: ' + e); }
  });
  blockedOverloads = [];
  log('[-] reverted ' + n + ' block(s). If anything looks off - restart game + re-attach.');
  return 'writes-open';
}

/* ---------- passive mode: heap polling, no hooks ---------- */

var passiveTimer = null;
var lastPassiveSig = null;
var seenLive = {}; // v3.2: dedupe of live snapshot content dumps

function passiveTick() {
  Java.perform(function () {
    [SMB_V1, SMB_V2].forEach(function (cn) {
      var K = null;
      try { K = Java.use(cn); } catch (e) { return; }
      Java.choose(cn, {
        onMatch: function (buf) {
          var b = buf;
          try { b = Java.cast(buf, K); } catch (e) {}
          var n = 0;
          try { n = b.getCount(); } catch (e) { return; } // probably released buffer
          if (n <= 0) return;
          var sig = n + '|';
          try { sig += jstr(b.get(0).getUniqueName()); } catch (e) { sig += '?'; }
          if (sig === lastPassiveSig) return;
          lastPassiveSig = sig;
          log('[passive] live ' + cn + ' with ' + n + ' snapshot(s):');
          for (var i = 0; i < n; i++) {
            try { dumpMeta(b.get(i), i); } catch (e) { log('  [' + i + '] ' + e); }
          }
        },
        onComplete: function () {}
      });
    });

    // v3.2: live Snapshot / SnapshotContents objects - the game's own open()
    // downloads content too; grab it from the heap as an alternate read path.
    ['com.google.android.gms.games.snapshot.SnapshotEntity',
     'com.google.android.gms.games.snapshot.SnapshotImpl',
     'com.google.android.gms.games.snapshot.SnapshotContentsEntity',
     'com.google.android.gms.games.snapshot.SnapshotContentsImpl'].forEach(function (cn) {
      var K = null;
      try { K = Java.use(cn); } catch (e) { return; }
      Java.choose(cn, {
        onMatch: function (obj) {
          try {
            var o = obj;
            try { o = Java.cast(obj, K); } catch (e) {}
            var liveMeta = null;
            try { liveMeta = retyped(o.getMetadata()); } catch (e) {}
            var contents = null;
            try { contents = retyped(o.getSnapshotContents()); } catch (e) {}
            if (contents === null || contents === undefined) {
              try { contents = retyped(o.getContents()); } catch (e) {}
            }
            if (contents === null || contents === undefined) return;
            var bytes = null, size = 0;
            try { bytes = contents.readFully(); } catch (e) {}
            if (bytes !== null && bytes !== undefined) size = bytes.length;
            if (size <= 0) return;
            var sig = '?';
            try { sig = jstr(liveMeta.getUniqueName()); } catch (e) {}
            sig = sig + '@' + size;
            if (seenLive[sig]) return;
            seenLive[sig] = true;
            log('[passive] LIVE ' + cn + ' -> ' + sig);
            if (liveMeta !== null && liveMeta !== undefined) dumpMeta(liveMeta, 'live');
            var p = saveBytes('live_' + sig.replace(/[^A-Za-z0-9_.@-]/g, '_') + '_' + Date.now() + '.bin', bytes);
            log('[passive] dumped ' + size + ' bytes -> ' + p + '  sha256=' + sha256(bytes));
          } catch (e) {}
        },
        onComplete: function () {}
      });
    });
  });
}

function passiveOn(intervalSec) {
  passiveOff();
  var sec = (intervalSec && intervalSec > 0) ? intervalSec : 3;
  passiveTimer = setInterval(passiveTick, sec * 1000);
  log('[+] passive heap polling every ' + sec + 's (Java.choose, NO hooks).');
  log('    Open the cloud-save / migration screen in the game - live buffers will be dumped.');
  log('    TIP: run blockWritesOn() first - the game may try to write while you browse.');
  return 'passive-on';
}

function passiveOff() {
  if (passiveTimer !== null) {
    clearInterval(passiveTimer);
    passiveTimer = null;
    log('[-] passive polling off');
  }
  return 'passive-off';
}

/* ---------- startup ---------- */

function status() {
  Java.perform(function () {
    [SMB_V1, SMB_V2, SC_LEGACY,
     'com.google.android.gms.games.PlayGames',
     'com.google.android.gms.tasks.Tasks'].forEach(function (cn) {
      try { Java.use(cn); log('[+] present: ' + cn); } catch (e) { log('[-] absent:  ' + cn); }
    });
  });
  return 'ok';
}

setTimeout(function () {
  Java.perform(function () {
    log('[*] frida_sk_snapshot_dump.js v3.2 - HOOKLESS (fix: conflict-policy try-chain, see header)');
    log('[*] REPL: listNow() | readSnap("<uniqueName>") | blockWritesOn() | passiveOn(3) | status()');
    log('[*] IMPORTANT: do NOT let the game "upload/overwrite" to cloud from a device with empty local saves.');
  });
}, 500);

rpc.exports = {
  listNow: listNow,
  readSnap: readSnap,
  blockWritesOn: blockWritesOn,
  blockWritesOff: blockWritesOff,
  passiveOn: passiveOn,
  passiveOff: passiveOff,
  status: status,
  rerun: function () { log('[i] v3 has no hooks to re-arm - use listNow()'); return 'noop'; }
};
