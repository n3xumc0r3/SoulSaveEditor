/*
 * frida_sk_snapshot_write.js  v3.3.2  (2026-11-11)
 * (runs in the GAME process - Soul Knight 8.7.0, legacy PGS v1 SDK)
 *
 * v3.2 -> v3.3 (WRITE PATH - the "b" experiment):
 *   v3.2 proved the read path: open(SnapshotMetadata) -> DataOrConflict ->
 *   getData() -> Snapshot -> getSnapshotContents().readFully(). The SAME
 *   open() returns a full Snapshot, so the write chain is short:
 *
 *     open(SnapshotMetadata) -> contents.writeBytes(data) ->
 *     client.commitAndClose(snapshot, metadataChange) -> verify
 *
 *   Safety design (think before typing):
 *     - open() goes ONLY through SnapshotMetadata (createIfMissing is never
 *       involved; a missing snapshot CANNOT be created by accident);
 *     - conflict on open() -> ABORT (no auto-resolve, no data touched);
 *     - before commitAndClose the script reads the buffer BACK and compares
 *       sha256 with what was written; mismatch -> NO commit;
 *     - if blockWritesOn() was armed, the script lifts it for the duration of
 *       OUR write and re-arms automatically afterwards (the game's own
 *       dual-write must not sneak in);
 *     - writeSnapNoop() writes back the EXACT bytes read from the cloud =
 *       a pure restore test: content does not change, only LastModifiedTimestamp;
 *     - the local save/dir files are never modified by this script.
 *
 * v3.3.0 -> v3.3.1 (first live run: read+open OK, sha matched the reference dump,
 *   but "writeBytes overloads: TypeError: not a function"):
 *   DIAGNOSIS: re-resolving SnapshotContents.writeBytes through
 *   Java.use(runtimeClass) is unreliable on this bridge - the v3.2 lesson for
 *   read* applies to write* too: call methods DIRECTLY on the typed wrapper.
 *   FIX: writeContents now logs the contents runtime class + its write* methods
 *   (always, for diagnosis), then tries in order:
 *     S1 direct contents.writeBytes(byte[])       <- proven call pattern
 *     S2 direct contents.writeBytes(byte[],int,int)
 *     S3 raw fd write: Os.lseek(fd,0,SEEK_SET) + Os.write + Os.ftruncate
 *        on contents.getParcelFileDescriptor() (no Java stream objects,
 *        nothing closed - the PFD stays valid for commitAndClose)
 *   Plus: a successful open is cached per uniqueName (a failed write leaves the
 *   snapshot open in the session; if a fresh open fails, the cached buffer is
 *   reused), the cache is dropped after a successful commit, and writeSnapNoop()
 *   auto-runs listNow() on success.
 *
 * v3.3.1 -> v3.3.2 (LIVE RESULT: S1 committed the noop write - 693800 bytes,
 *   sha verified, fresh LastModifiedTimestamp confirmed by listNow. WRITE PATH
 *   PROVEN. Two cosmetic defects observed in that run are fixed here):
 *   - getClass().getName() is NOT callable on typed wrappers in this bridge
 *     (metadata dumps show "?" the same way) even though the objects' declared
 *     methods work fine. The class-name diagnostic now falls back to the Frida
 *     wrapper property $className, and Java.use() is called only on a name
 *     that looks like a real class - no more ClassNotFoundException on "?".
 *   - saveBytes() skips directories that do not exist after mkdirs() (scoped
 *     storage: /sdcard/skdump can never be created from an app process)
 *     instead of failing with a scary FileNotFoundException - the app-specific
 *     fallback dir remains the proven dump location.
 *
 * v3.2 changelog (kept): policy try-chain rejected by this server/client combo
 *   (all explicit policies -> "Must provide a valid conflict policy!"), so
 *   md-open is the primary and only path; read chain readFully -> readFully(byte[])
 *   -> dup(PFD); save dir fallback chain; hookless policy (REPL + Java.choose only).
 *
 * Commands (REPL):
 *   listNow()                      - metadata of all snapshots (read-only)
 *   readSnap('<uniqueName>')       - download contents to skdump (read-only)
 *   writeSnapNoop('<uniqueName>')  - WRITE-BACK of the very same bytes that are
 *                                    in the cloud right now (restore/write test)
 *   writeSnapFile('<uniqueName>', '<path-on-device>')
 *                                  - WRITE the file content into the snapshot and
 *                                    commit (e.g. patched save pushed via adb)
 *   writeStatus()                  - blockWrites state + last written sha
 *   blockWritesOn()/Off()          - cloud write blocker (v3.2, unchanged)
 *   passiveOn(3)/passiveOff()      - heap polling (v3.2, unchanged)
 *   status()                       - SDK class probe
 *
 * Typical session (variant b):
 *   1) game running, main menu; frida -U -n "Soul Knight" -l frida_sk_snapshot_write.js
 *   2) listNow()                        -> confirm SoulKnightCloud, note LastModified
 *   3) writeSnapNoop('SoulKnightCloud') -> WRITE TEST (same bytes back);
 *                                          check "commit OK" + new LastModified
 *   4) optional real write:
 *      adb push cloud_patched.bin /sdcard/Android/data/com.ChillyRoom.DungeonShooter/files/skdump/
 *      writeSnapFile('SoulKnightCloud', '/sdcard/Android/data/com.ChillyRoom.DungeonShooter/files/skdump/cloud_patched.bin')
 *   5) listNow() again / restart game and check the save actually loads.
 */

'use strict';

var SMB_V1    = 'com.google.android.gms.games.snapshot.SnapshotMetadataBuffer';
var SMB_V2    = 'com.google.android.gms.games.snapshots.SnapshotMetadataBuffer';
var SC_LEGACY = 'com.google.android.gms.games.SnapshotsClient';
var OUT_DIR   = '/sdcard/skdump';

function log(s) { console.log(s); }
function jstr(x) { try { return '' + x; } catch (e) { return '<unprintable>'; } }

function safe(what, fn) {
  try { return fn(); } catch (e) { log('    [!] ' + what + ': ' + e); return undefined; }
}

/* ---------- helpers (v3.2, unchanged) ---------- */

// getClass() is not callable on some typed wrappers in this Frida/PGS combo;
// the Frida wrapper property $className still knows the runtime class.
function classNameOf(obj) {
  var n = null;
  try { n = '' + obj.getClass().getName(); } catch (e) {}
  if (n === null || n === 'undefined' || n === 'null') {
    try { n = '' + obj.$className; } catch (e2) {}
  }
  if (n === 'undefined' || n === 'null') n = null;
  return n;
}

function retyped(obj) {
  if (obj === null || obj === undefined) return obj;
  var name = null;
  try { name = obj.getClass().getName(); } catch (e) { return obj; }
  if (name === null || name === 'java.lang.Object') return obj;
  try { return Java.cast(obj, Java.use(name)); } catch (e) { return obj; }
}

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

function smartStr(v) {
  if (v === null || v === undefined) return undefined;
  if (typeof v !== 'object') return jstr(v);
  var o = retyped(v);
  var id = null, name = null;
  try { id = jstr(o.getPlayerId()); } catch (e) {}
  try { name = jstr(o.getDisplayName()); } catch (e) {}
  if (id === 'null' || id === 'undefined') id = null;
  if (name === 'null' || name === 'undefined') name = null;
  if (id !== null || name !== null) return 'player "' + (name || '?') + '" (#' + (id || '?') + ')';
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
    } catch (e) {}
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

var POLICY_ORDER = [
  'RESOLUTION_POLICY_MOST_RECENTLY_MODIFIED',
  'RESOLUTION_POLICY_LONGEST_PLAYTIME',
  'RESOLUTION_POLICY_LAST_KNOWN_GOOD',
  'RESOLUTION_POLICY_HIGHEST_PROGRESS',
  'RESOLUTION_POLICY_MANUAL'
];
var POLICY_FALLBACK_VALUES = {
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

function taskFailure(task, what) {
  return safe(what + '.getException', function () {
    var ex = task.getException();
    if (ex === null || ex === undefined) { log('    task finished with no result and no exception'); return; }
    log('    task failed: ' + ex.getClass().getName() + ': ' + jstr(ex.getMessage()));
    try { log('    status code = ' + ex.getStatusCode()); } catch (e) {}
    return ex;
  });
}

function loadMetaBuffer(client) {
  var task = client.load(true);
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
      if (!d.exists()) d.mkdirs(); // best effort; scoped storage may forbid it
      if (!d.isDirectory()) {
        log('    [i] skip ' + d.getAbsolutePath() + ' (not accessible under scoped storage)');
        continue;
      }
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

function readFileBytes(path) {
  return safe('readFileBytes(' + path + ')', function () {
    var fis = Java.use('java.io.FileInputStream').$new(path);
    var baos = Java.use('java.io.ByteArrayOutputStream').$new();
    var buf = Java.array('byte', new Array(65536).fill(0));
    var total = 0;
    while (true) {
      var n = fis.read(buf);
      if (n < 0) break;
      if (n > 0) { baos.write(buf, 0, n); total += n; }
    }
    fis.close();
    log('    [i] file ' + path + ' = ' + total + ' bytes');
    return baos.toByteArray();
  });
}

/* ---------- cloud-write blocker (v3.2, unchanged behaviour) ---------- */

var blockedOverloads = [];

function blockWritesOn() {
  return Java.perform(function () {
    var Tasks = Java.use('com.google.android.gms.tasks.Tasks');
    var n = 0;
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

// Lift the blocker for OUR own write, run fn, re-arm afterwards.
function withWritesUnblocked(fn) {
  var wasBlocked = blockedOverloads.length > 0;
  if (wasBlocked) {
    log('[i] temporarily lifting write-block for OUR write ...');
    blockWritesOff();
  }
  var out;
  try { out = fn(); }
  finally {
    if (wasBlocked) {
      log('[i] re-arming write-block ...');
      safe('re-arm blockWritesOn', blockWritesOn);
    }
  }
  return out;
}

/* ---------- passive mode (v3.2, unchanged) ---------- */

var passiveTimer = null;
var lastPassiveSig = null;
var seenLive = {};

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
          try { n = b.getCount(); } catch (e) { return; }
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
  });
}

function passiveOn(intervalSec) {
  passiveOff();
  var sec = (intervalSec && intervalSec > 0) ? intervalSec : 3;
  passiveTimer = setInterval(passiveTick, sec * 1000);
  log('[+] passive heap polling every ' + sec + 's (Java.choose, NO hooks).');
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

/* ---------- v3.3: WRITE PATH ---------- */

var LAST_WRITE = { uniqueName: null, sha: null, size: 0, at: null };
var OPEN_CACHE = {}; // uniqueName -> open context; survives a failed write (snapshot stays open)

function listNow() {
  return Java.perform(function () {
    log('[*] listNow: probing SDK classes ...');
    var act = findActivity();
    if (act === null || act === undefined) {
      log('[!] no live Activity. Launch Soul Knight first.');
      return 'no-activity';
    }
    var client = makeSnapshotsClient(act);
    if (client === null || client === undefined) return 'no-client';

    var task = safe('client.load(true)', function () { return client.load(true); });
    if (task === null || task === undefined) return 'no-load-task';

    var result = safe('Tasks.await', function () { return awaitTask(task, 30, 'load'); });
    if (result === null || result === undefined) {
      taskFailure(task, 'load');
      return 'load-failed';
    }
    var bufObj = unwrapOne(retyped(result));
    var buf = null;
    try { buf = Java.cast(bufObj, Java.use(SMB_V1)); }
    catch (eV1) {
      try { buf = Java.cast(bufObj, Java.use(SMB_V2)); }
      catch (eV2) { log('[!] result is not a SnapshotMetadataBuffer: ' + eV1 + ' / ' + eV2); return 'not-a-buffer'; }
    }
    var n = dumpBuffer(buf, 'GPGS snapshots of the signed-in account');
    return n + ' snapshot(s)';
  });
}

// Open a snapshot STRICTLY via SnapshotMetadata (no createIfMissing anywhere).
// Returns {client, snap, contents, meta, bytes, uniqueName} or null.
function openSnapForWrite(uniqueName) {
  var act = findActivity();
  if (act === null || act === undefined) { log('[!] no live Activity'); return null; }
  var client = makeSnapshotsClient(act);
  if (client === null || client === undefined) return null;

  var SC = Java.use(SC_LEGACY);

  // metadata first: without it the md-open path is impossible
  var mdFound = null;
  safe('metadata pre-check', function () {
    mdFound = findMetaByName(client, uniqueName);
    if (mdFound === null || mdFound === undefined) log('[!] no metadata entry for "' + uniqueName + '"');
    return true;
  });
  if (mdFound === null || mdFound === undefined) return null;

  // pick open(SnapshotMetadata) 1-arg, fallback open(md, int)
  var md1 = null, mdInt = null;
  SC.open.overloads.forEach(function (o) {
    var ts = o.argumentTypes.map(function (t) { return '' + t.type; });
    if (ts.length === 1) md1 = o;
    if (ts.length === 2 && (ts[1] === 'int' || ts[1] === 'int32')) mdInt = o;
  });

  var doc = null, how = null;
  if (md1 !== null) {
    log('[i] trying open(SnapshotMetadata) ...');
    doc = safe('open(md)', function () {
      var task = md1.call(client, mdFound);
      var d = awaitTask(task, 30, 'open(md)');
      log('    [+] open(SnapshotMetadata) OK');
      return retyped(d);
    });
    if (doc !== null && doc !== undefined) how = 'open(md)';
  }
  if ((doc === null || doc === undefined) && mdInt !== null) {
    var policies = [];
    POLICY_ORDER.forEach(function (pn) {
      var v;
      try { v = SC[pn].value; } catch (e) { v = POLICY_FALLBACK_VALUES[pn]; }
      policies.push({ name: pn, value: v });
    });
    for (var j = 0; j < policies.length && (doc === null || doc === undefined); j++) {
      var pp = policies[j];
      log('[i] trying open(md, ' + pp.name.split('RESOLUTION_POLICY_')[1] + '=' + pp.value + ') ...');
      doc = safe('open(md,' + pp.value + ')', function () {
        var task = mdInt.call(client, mdFound, pp.value);
        var d = awaitTask(task, 30, 'open(md, policy)');
        log('    [+] open(md, ' + pp.value + ') OK');
        return retyped(d);
      });
      if (doc !== null && doc !== undefined) how = 'open(md,' + pp.value + ')';
    }
  }
  if (doc === null || doc === undefined) {
    log('[!] fresh open() failed - trying cached open (a failed write leaves the snapshot open) ...');
    var cached = OPEN_CACHE[uniqueName];
    if (cached !== null && cached !== undefined) {
      var probe = safe('cache probe readFully', function () { return cached.contents.readFully(); });
      if (probe !== null && probe !== undefined && probe.length > 0) {
        log('[+] reusing cached open for "' + uniqueName + '" (' + probe.length + ' bytes readable)');
        return cached;
      }
    }
    log('[!] open() failed for "' + uniqueName + '" and no usable cache - paste this log back to me');
    return null;
  }
  log('[+] opened "' + uniqueName + '" via ' + how);

  // conflict -> ABORT (never auto-resolve)
  var isConflict = safe('isConflict()', function () { return doc.isConflict(); });
  if (isConflict === true) {
    var c = retyped(safe('getConflict()', function () { return doc.getConflict(); }));
    if (c !== null && c !== undefined) dumpConflictAny(c, uniqueName);
    log('[!] ABORT: conflict present - resolve it consciously, not automatically.');
    return null;
  }

  var snap = retyped(safe('getData()', function () { return doc.getData(); }));
  if (snap === null || snap === undefined) { log('[!] DataOrConflict.getData() = null'); return null; }

  var meta = null;
  safe('snapshot accessors', function () { meta = snap.getMetadata(); });
  if (meta !== null && meta !== undefined) { log('[i] metadata (pre-write):'); dumpMeta(retyped(meta), 0); }

  var contents = getSnapshotContentsAny(snap);
  if (contents === null || contents === undefined) { log('[!] no SnapshotContents'); return null; }

  var w = { client: client, snap: snap, contents: contents, meta: meta, uniqueName: uniqueName };
  OPEN_CACHE[uniqueName] = w;
  return w;
}

// Pick commitAndClose(Snapshot, SnapshotMetadataChange) by signature.
function pickCommitOverload(client) {
  var SC = Java.use(SC_LEGACY);
  var chosen = null;
  var sigs = [];
  safe('commitAndClose overloads', function () {
    SC.commitAndClose.overloads.forEach(function (o) {
      var ts = o.argumentTypes.map(function (t) { return '' + t.type; });
      sigs.push('(' + ts.join(', ') + ')');
      if (ts.length === 2 && chosen === null) chosen = o;
    });
  });
  log('[i] commitAndClose overloads: ' + (sigs.join(' | ') || '<none>'));
  if (chosen === null) log('[!] no 2-arg commitAndClose found - paste the list above back to me');
  return chosen;
}

function makeEmptyMetadataChange() {
  var Builder = safe('SnapshotMetadataChange$Builder', function () {
    return Java.use('com.google.android.gms.games.snapshot.SnapshotMetadataChange$Builder');
  });
  if (Builder === null || Builder === undefined) {
    log('[!] no SnapshotMetadataChange$Builder in this SDK - paste log back');
    return null;
  }
  return Builder.$new().build();
}

function commitAndClose(w, label) {
  var change = makeEmptyMetadataChange();
  if (change === null || change === undefined) return 'no-change-obj';

  var ovl = pickCommitOverload(w.client);
  if (ovl === null) return 'no-overload';

  log('[*] commitAndClose ...');
  var task = safe('commitAndClose', function () { return ovl.call(w.client, w.snap, change); });
  if (task === null || task === undefined) return 'commit-call-failed';

  var result = null;
  try { result = awaitTask(task, 90, 'commit'); }
  catch (e) {
    log('[!] commit await: ' + e);
    taskFailure(task, 'commit');
    return 'commit-failed';
  }
  if (result === null || result === undefined) {
    taskFailure(task, 'commit');
    return 'commit-failed';
  }

  var md = retyped(result);
  log('[+] ' + label + ': COMMIT OK');
  try {
    var ts = md.getLastModifiedTimestamp();
    log('    new LastModifiedTimestamp = ' + ts +
        (ts ? '  (' + new Date(parseInt('' + ts, 10)).toISOString() + ')' : ''));
    try { log('    new PlayedTime = ' + md.getPlayedTime()); } catch (e) {}
  } catch (e) {}
  return 'commit-ok';
}

// Core: open -> writeBytes(newBytes) -> read back -> sha compare -> commit.
function writeBytesAndCommit(uniqueName, newBytes, label) {
  return withWritesUnblocked(function () {
    var w = openSnapForWrite(uniqueName);
    if (w === null) return 'open-failed';
    return writeContents(w, newBytes, sha256(newBytes), label);
  });
}

function writeContents(w, newBytes, curSha, label) {
  // whole open->write->verify->commit chain runs INSIDE the unblocked window

  // ALWAYS diagnose: runtime class + write* methods (v3.3.0 failed silently
  // inside class re-resolution - from now on we always see what we deal with)
  var contentsCls = classNameOf(w.contents);
  if (contentsCls === null || contentsCls === undefined) contentsCls = '?';
  log('[i] contents runtime class: ' + contentsCls);
  safe('diagnose write methods', function () {
    if (!/^[A-Za-z_$][\w$]*(\.[\w$]+)+$/.test(contentsCls)) {
      log('    [i] class name unavailable (bridge quirk) - skipping method dump; the S1 direct call does not need it');
      return true;
    }
    var cls = Java.use(contentsCls);
    var ms = cls.class.getDeclaredMethods();
    var names = [];
    for (var i = 0; i < ms.length; i++) {
      var mn = '' + ms[i].getName();
      if (mn.indexOf('write') === 0) {
        var pts = ms[i].getParameterTypes(), ps = [];
        for (var k = 0; k < pts.length; k++) ps.push('' + pts[k].getName());
        names.push(mn + '(' + ps.join(',') + ')');
      }
    }
    log('[i] write* methods on contents class: ' + (names.join(' | ') || '<NONE>'));
    return true;
  });

  var ok = false, how = null;

  // S1: DIRECT call on the typed wrapper - the proven v3.2 read-pattern
  // (readFully() worked exactly this way; no Java.use re-resolution involved)
  ok = safe('S1 contents.writeBytes(byte[])', function () {
    return w.contents.writeBytes(newBytes) === true;
  }) === true;
  if (ok) how = 'S1 writeBytes(byte[])';

  // S2: 3-arg direct
  if (!ok) {
    ok = safe('S2 contents.writeBytes(byte[],int,int)', function () {
      return w.contents.writeBytes(newBytes, 0, newBytes.length) === true;
    }) === true;
    if (ok) how = 'S2 writeBytes(byte[],int,int)';
  }

  // S3: raw fd write - SDK-independent, no Java stream objects, nothing closed
  // (the PFD must stay valid for commitAndClose); lseek(0) because readFully
  // leaves the shared offset at EOF.
  if (!ok) {
    ok = safe('S3 raw fd write', function () {
      var pfd = w.contents.getParcelFileDescriptor();
      var fd = pfd.getFileDescriptor();
      var Os = Java.use('android.system.Os');
      var OsConstants = Java.use('android.system.OsConstants');
      Os.lseek(fd, 0, OsConstants.SEEK_SET.value);
      Os.write(fd, newBytes, 0, newBytes.length);
      Os.ftruncate(fd, newBytes.length);
      return true;
    }) === true;
    if (ok) how = 'S3 raw fd (lseek0 + write + ftruncate)';
  }

  if (!ok) { log('[!] ALL write strategies failed - nothing committed. Paste the write* method list above back to me.'); return 'write-failed'; }
  log('[+] write OK via ' + how + ' (' + newBytes.length + ' bytes into the buffer)');

  // VERIFY: read the buffer back before committing anything
  var back = readBytesAny(w.contents);
  if (back === null || back === undefined || sha256(back) !== curSha) {
    log('[!] VERIFY FAILED - buffer does not hold what we wrote. NO COMMIT.');
    if (back !== null && back !== undefined) {
      var pf = saveBytes('verifyfail_' + w.uniqueName + '_' + Date.now() + '.bin', back);
      log('[i] buffer content saved for analysis: ' + pf);
    }
    return 'sha-mismatch';
  }
  log('[+] VERIFY OK (buffer sha == ' + curSha + ') - committing');

  var rc = commitAndClose(w, label);
  if (rc === 'commit-ok') {
    LAST_WRITE = { uniqueName: w.uniqueName, sha: curSha, size: newBytes.length, at: new Date().toISOString() };
    saveBytes('written_' + w.uniqueName + '_' + Date.now() + '.bin', newBytes);
    if (OPEN_CACHE[w.uniqueName] !== undefined) delete OPEN_CACHE[w.uniqueName]; // commit closed it
  }
  return rc;
}

function writeSnapNoop(uniqueName) {
  return Java.perform(function () {
    if (uniqueName === null || uniqueName === undefined || '' + uniqueName === '') uniqueName = 'SoulKnightCloud';
    uniqueName = '' + uniqueName;
    log('[*] writeSnapNoop("' + uniqueName + '"): read cloud bytes -> write SAME bytes back -> commit');

    return withWritesUnblocked(function () {
      var w = openSnapForWrite(uniqueName);
      if (w === null) return 'open-failed';

      var bytes = readBytesAny(w.contents);
      if (bytes === null || bytes === undefined || bytes.length === 0) {
        log('[!] cannot read current content - ABORT (will not write zeros)');
        return 'empty';
      }
      var curSha = sha256(bytes);
      log('[+] current cloud content: ' + bytes.length + ' bytes');
      log('[+] current sha256 = ' + curSha);
      log('[+] head(32) = ' + headHex(bytes, 32));

      var rc = writeContents(w, bytes, curSha, 'noop');
      if (rc === 'commit-ok') {
        log('[*] WRITE PATH WORKS. Restore/edit is now proven possible.');
        log('[*] auto-verify via listNow() (LastModifiedTimestamp must be fresh):');
        safe('auto listNow', function () { listNow(); });
      }
      return rc;
    });
  });
}

function writeSnapFile(uniqueName, path) {
  return Java.perform(function () {
    if (uniqueName === null || uniqueName === undefined || '' + uniqueName === '') uniqueName = 'SoulKnightCloud';
    uniqueName = '' + uniqueName;
    if (path === null || path === undefined || '' + path === '') {
      log('[!] usage: writeSnapFile("<uniqueName>", "<path-on-device>")');
      log('    e.g. writeSnapFile("SoulKnightCloud", "/sdcard/Android/data/com.ChillyRoom.DungeonShooter/files/skdump/cloud_patched.bin")');
      return 'bad-args';
    }
    path = '' + path;
    log('[*] writeSnapFile("' + uniqueName + '", "' + path + '")');

    var fbytes = readFileBytes(path);
    if (fbytes === null || fbytes === undefined || fbytes.length === 0) {
      log('[!] file empty/unreadable - ABORT');
      return 'empty-file';
    }
    var s256 = sha256(fbytes);
    log('[+] file sha256 = ' + s256);
    log('[+] file head(32) = ' + headHex(fbytes, 32));
    var head = headHex(fbytes, 4).replace(/ /g, '');
    if (head === '7b002200') {
      log('[i] file starts with UTF-16LE JSON "{" - format matches the cloud save');
    } else if (head === '7b22') {
      log('[i] note: file looks like ASCII JSON (not UTF-16LE) - pushing as-is');
    } else {
      log('[i] note: file does not start with JSON "{" - pushing as-is (arbitrary blob restore)');
    }
    if (fbytes.length > 2.5 * 1024 * 1024) {
      log('[!] file > 2.5MB - GPGS size limit risk. ABORT.');
      return 'too-big';
    }

    var rc = writeBytesAndCommit(uniqueName, fbytes, 'file:' + path);
    if (rc === 'commit-ok') {
      log('[*] FILE PUSHED to "' + uniqueName + '" (' + fbytes.length + ' bytes)');
      log('[i] verify with: listNow()  then restart the game and load the save');
    }
    return rc;
  });
}

function writeStatus() {
  log('[i] write-block armed: ' + (blockedOverloads.length > 0 ? 'YES (' + blockedOverloads.length + ' overloads)' : 'no'));
  log('[i] last successful write: ' + JSON.stringify(LAST_WRITE));
  return 'ok';
}

function readSnap(uniqueName) {
  return Java.perform(function () {
    if (uniqueName === null || uniqueName === undefined || '' + uniqueName === '') {
      log('[!] usage: readSnap("<uniqueName>")');
      return 'bad-args';
    }
    uniqueName = '' + uniqueName;
    var w = openSnapForWrite(uniqueName);
    if (w === null) return 'open-failed';
    var bytes = readBytesAny(w.contents);
    if (bytes === null || bytes === undefined || bytes.length === 0) { log('[!] empty content'); return 'empty'; }
    var p = saveBytes('snap_' + uniqueName + '_' + Date.now() + '.bin', bytes);
    log('[+] saved: ' + p + ' (' + bytes.length + ' bytes)');
    log('[+] sha256   = ' + sha256(bytes));
    log('[+] head(32) = ' + headHex(bytes, 32));
    return p;
  });
}

/* ---------- startup ---------- */

function status() {
  Java.perform(function () {
    [SMB_V1, SMB_V2, SC_LEGACY,
     'com.google.android.gms.games.PlayGames',
     'com.google.android.gms.tasks.Tasks',
     'com.google.android.gms.games.snapshot.SnapshotMetadataChange$Builder'].forEach(function (cn) {
      try { Java.use(cn); log('[+] present: ' + cn); } catch (e) { log('[-] absent:  ' + cn); }
    });
  });
  return 'ok';
}

setTimeout(function () {
  Java.perform(function () {
    log('[*] frida_sk_snapshot_write.js v3.3.2 - WRITE PATH PROVEN (noop commit 11.11.2026; hookless; REPL-driven)');
    log('[*] READ:   listNow() | readSnap("<uniqueName>")');
    log('[*] WRITE:  writeSnapNoop("SoulKnightCloud")   <- start with THIS one (same bytes back)');
    log('[*]         writeSnapFile("<uniqueName>", "<path-on-device>")');
    log('[*]         writeStatus() | blockWritesOn()/Off() | passiveOn(3) | status()');
    log('[*] SAFETY: conflict -> abort; sha verified before commit; writes blocked unless you ask.');
  });
}, 500);

rpc.exports = {
  listNow: listNow,
  readSnap: readSnap,
  writeSnapNoop: writeSnapNoop,
  writeSnapFile: writeSnapFile,
  writeStatus: writeStatus,
  blockWritesOn: blockWritesOn,
  blockWritesOff: blockWritesOff,
  passiveOn: passiveOn,
  passiveOff: passiveOff,
  status: status
};
