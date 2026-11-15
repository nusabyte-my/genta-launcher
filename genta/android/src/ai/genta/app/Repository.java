package ai.genta.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.util.Base64;
import org.json.*;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.time.*;
import java.util.*;
import java.util.regex.*;
import javax.crypto.*;
import javax.crypto.spec.GCMParameterSpec;
import android.security.keystore.*;

/** Offline-first state and compatible per-field Lamport synchronization events. */
public final class Repository {
    private static Repository instance;
    public static synchronized Repository get(Context context) {
        if(instance==null)instance=new Repository(context.getApplicationContext());
        return instance;
    }
    private final SharedPreferences prefs;
    private JSONObject tasks, notes, focus, fields, versions;
    private JSONArray events;
    private final Set<String> eventIds=new HashSet<>();
    private final String device;
    private long clock;
    public Repository(Context context) {
        prefs=context.getSharedPreferences("genta",Context.MODE_PRIVATE);
        device=prefs.getString("device",UUID.randomUUID().toString());
        try {
            tasks=new JSONObject(prefs.getString("tasks","{}")); notes=new JSONObject(prefs.getString("notes","{}"));
            focus=new JSONObject(prefs.getString("focus","{}")); fields=new JSONObject(prefs.getString("fields","{}"));
            versions=new JSONObject(prefs.getString("versions","{}")); events=new JSONArray(prefs.getString("events","[]"));
        } catch(JSONException error) { throw new IllegalStateException("Planner data is unreadable; do not clear storage before exporting a backup.",error); }
        for(int i=0;i<events.length();i++)eventIds.add(events.optJSONObject(i).optString("event_id"));
        clock=prefs.getLong("clock",0);
        prefs.edit().putString("device",device).apply();
    }
    private static void put(JSONObject obj,String key,Object value) { try {obj.put(key,value==null?JSONObject.NULL:value);}catch(JSONException e){throw new IllegalArgumentException(e);} }
    private synchronized void persist() {
        boolean ok=prefs.edit().putString("tasks",tasks.toString()).putString("notes",notes.toString()).putString("focus",focus.toString()).putString("fields",fields.toString()).putString("versions",versions.toString()).putString("events",events.toString()).putLong("clock",clock).commit();
        if(!ok)throw new IllegalStateException("Could not save planner data");
    }
    public static JSONObject object(Object... pairs) {
        JSONObject result=new JSONObject(); for(int i=0;i<pairs.length;i+=2)put(result,(String)pairs[i],pairs[i+1]);return result;
    }
    private synchronized void emit(String kind,String entity,JSONObject values) {
        clock++;
        JSONObject event=object("event_id",UUID.randomUUID().toString(),"device",device,"clock",clock,"kind",kind,"entity",entity,"values",values);
        apply(event); persist();
    }
    private boolean newer(String key,long incoming,String source) {
        JSONObject stamp=fields.optJSONObject(key);
        return stamp==null || incoming>stamp.optLong("clock") || incoming==stamp.optLong("clock") && source.compareTo(stamp.optString("device"))>0;
    }
    private void apply(JSONObject event) {
        String id=event.optString("event_id");
        if(eventIds.contains(id))return;
        UUID.fromString(id);String source=event.optString("device");UUID.fromString(source);
        long incoming=event.optLong("clock");if(incoming<=0||incoming>9007199254740991L)throw new IllegalArgumentException("Invalid sync clock");
        String kind=event.optString("kind"), entity=event.optString("entity");JSONObject values=event.optJSONObject("values");
        if(values==null)throw new IllegalArgumentException("Invalid sync values");
        JSONObject target;
        if(kind.equals("task")) {
            UUID.fromString(entity);target=tasks.optJSONObject(entity);
            if(target==null)target=defaults(entity,values.optString("title","Untitled task"),null);
            Set<String> allowed=new HashSet<>(Arrays.asList("title","day","due_at","priority","done","completed_at","created_at","deadline","project","repeat_rule","estimate_minutes","deleted"));
            Iterator<String> check=values.keys();while(check.hasNext())if(!allowed.contains(check.next()))throw new IllegalArgumentException("Invalid task field");
        } else if(kind.equals("note")) {
            LocalDate.parse(entity);target=object("text",notes.optString(entity,""));
            if(values.length()!=1||!values.has("text"))throw new IllegalArgumentException("Invalid note");
            JSONArray history=versions.optJSONArray(entity);if(history==null)history=new JSONArray();
            history.put(object("event_id",id,"text",values.optString("text")));put(versions,entity,history);
        } else if(kind.equals("focus")) {
            UUID.fromString(entity);target=focus.optJSONObject(entity);if(target==null)target=new JSONObject();
            if(values.optLong("seconds")<=0||values.optLong("seconds")>86400)throw new IllegalArgumentException("Invalid focus record");
        } else throw new IllegalArgumentException("Unknown sync entity");
        boolean deleted=target.optInt("deleted")==1;
        Iterator<String> keys=values.keys();while(keys.hasNext()) {
            String key=keys.next(), stampKey=kind+":"+entity+":"+key;
            if(newer(stampKey,incoming,source)) {
                Object value=values.opt(key);
                if((key.equals("done")||key.equals("deleted"))&&value instanceof Boolean)value=((Boolean)value)?1:0;
                if(kind.equals("task")&&(key.equals("due_at")||key.equals("done")&&Integer.valueOf(0).equals(value)))prefs.edit().remove("reminded:"+entity).apply();
                put(target,key,value);
                put(fields,stampKey,object("clock",incoming,"device",source));
            }
        }
        if(deleted)put(target,"deleted",1);
        if(kind.equals("task"))put(tasks,entity,target);else if(kind.equals("note"))put(notes,entity,target.optString("text"));else put(focus,entity,target);
        clock=Math.max(clock,incoming);put(event,"seq",events.length()+1);events.put(event);eventIds.add(id);
    }
    private static JSONObject defaults(String uid,String title,String day) {
        return object("uid",uid,"title",title,"day",day,"due_at",null,"priority",0,"done",0,"completed_at",null,"created_at",LocalDateTime.now().withNano(0).toString(),"deadline",null,"project","","repeat_rule","none","estimate_minutes",25,"deleted",0);
    }
    public synchronized ArrayList<JSONObject> tasks(String filter) {
        ArrayList<JSONObject> result=new ArrayList<>();Iterator<String> keys=tasks.keys();String today=LocalDate.now().toString();
        while(keys.hasNext()) {
            JSONObject task=tasks.optJSONObject(keys.next());if(task.optInt("deleted")==1)continue;
            String day=task.isNull("day")?null:task.optString("day");
            if(filter.equals("today")&&(day==null||day.compareTo(today)>0))continue;
            if(filter.equals("inbox")&&day!=null)continue;
            result.add(snapshot(task));
        }
        result.sort((a,b)->{int order=Integer.compare(b.optInt("priority"),a.optInt("priority"));return order!=0?order:a.optString("created_at").compareTo(b.optString("created_at"));});return result;
    }
    private static JSONObject snapshot(JSONObject source) {
        if(source==null)return null;
        try{return new JSONObject(source.toString());}catch(JSONException error){throw new IllegalStateException(error);}
    }
    public synchronized JSONObject get(String uid) {return snapshot(tasks.optJSONObject(uid));}
    public synchronized String add(String text,String defaultDay) {
        text=text.trim();if(text.isEmpty())throw new IllegalArgumentException("Write a task first");
        String day=defaultDay,due=null;
        Pattern p=Pattern.compile("(?i)\\s+(?:(today|tomorrow|monday|tuesday|wednesday|thursday|friday|saturday|sunday)(?:\\s+at\\s+(\\d{1,2})(?::(\\d{2}))?\\s*(am|pm)?)?|at\\s+(\\d{1,2})(?::(\\d{2}))?\\s*(am|pm)?)\\s*$");
        Matcher m=p.matcher(text);
        if(m.find()) {
            String word=m.group(1)==null?"":m.group(1).toLowerCase(Locale.ROOT);
            LocalDate target=LocalDate.now();
            if(word.equals("tomorrow"))target=target.plusDays(1);
            else if(!word.isEmpty()&&!word.equals("today")) {int wanted=Arrays.asList("monday","tuesday","wednesday","thursday","friday","saturday","sunday").indexOf(word)+1;target=target.plusDays((wanted-target.getDayOfWeek().getValue()+7)%7);}
            String hour=m.group(2)!=null?m.group(2):m.group(5), minute=m.group(3)!=null?m.group(3):m.group(6), period=m.group(4)!=null?m.group(4):m.group(7);
            if(hour!=null) {
                int h=Integer.parseInt(hour),min=minute==null?0:Integer.parseInt(minute);
                if(period!=null){if(h<1||h>12)throw new IllegalArgumentException("Invalid time");h=h%12+(period.equalsIgnoreCase("pm")?12:0);}
                LocalDateTime stamp=LocalDateTime.of(target,LocalTime.of(h,min));
                if(stamp.isBefore(LocalDateTime.now())) {if(word.isEmpty())stamp=stamp.plusDays(1);else if(!word.equals("today")&&!word.equals("tomorrow"))stamp=stamp.plusDays(7);}
                due=stamp.withNano(0).toString();target=stamp.toLocalDate();
            }
            day=target.toString();text=text.substring(0,m.start()).trim();if(text.isEmpty())throw new IllegalArgumentException("A task needs a title");
        }
        String uid=UUID.randomUUID().toString();JSONObject value=defaults(uid,text,day);put(value,"due_at",due);value.remove("uid");emit("task",uid,value);return uid;
    }
    public synchronized void update(String uid,JSONObject changes) {
        JSONObject old=get(uid);if(old==null)throw new IllegalArgumentException("Task no longer exists");
        boolean wasDone=old.optInt("done")==1;
        if(changes.has("done")) {
            boolean done=changes.optInt("done")==1;
            put(changes,"completed_at",done?LocalDateTime.now().withNano(0).toString():null);
            emit("task",uid,changes);if(done&&!wasDone)repeat(uid,old);return;
        }
        emit("task",uid,changes);
    }
    private void repeat(String uid,JSONObject task) {
        String rule=task.optString("repeat_rule","none");if(rule.equals("none"))return;
        LocalDate base=task.isNull("day")?LocalDate.now():LocalDate.parse(task.optString("day"));if(base.isBefore(LocalDate.now()))base=LocalDate.now();
        LocalDate next=rule.equals("weekly")?base.plusDays(7):rule.equals("monthly")?base.plusMonths(1):base.plusDays(1);
        while(rule.equals("weekdays")&&next.getDayOfWeek().getValue()>5)next=next.plusDays(1);
        String nextUid=uuid5("genta:"+uid+":"+next);if(tasks.has(nextUid))return;
        JSONObject value=defaults(nextUid,task.optString("title"),next.toString());value.remove("uid");
        for(String key:Arrays.asList("priority","project","repeat_rule","estimate_minutes"))put(value,key,task.opt(key));
        if(!task.isNull("due_at"))put(value,"due_at",LocalDateTime.of(next,LocalDateTime.parse(task.optString("due_at")).toLocalTime()).toString());
        if(!task.isNull("deadline"))put(value,"deadline",next.plusDays(java.time.temporal.ChronoUnit.DAYS.between(base,LocalDate.parse(task.optString("deadline")))).toString());
        emit("task",nextUid,value);
    }
    private static String uuid5(String name) {
        try {UUID ns=UUID.fromString("6ba7b811-9dad-11d1-80b4-00c04fd430c8");MessageDigest sha=MessageDigest.getInstance("SHA-1");sha.update(ByteBuffer.allocate(16).putLong(ns.getMostSignificantBits()).putLong(ns.getLeastSignificantBits()).array());byte[] bytes=sha.digest(name.getBytes(StandardCharsets.UTF_8));bytes[6]=(byte)((bytes[6]&15)|80);bytes[8]=(byte)((bytes[8]&63)|128);ByteBuffer b=ByteBuffer.wrap(bytes);return new UUID(b.getLong(),b.getLong()).toString();}catch(NoSuchAlgorithmException e){throw new IllegalStateException(e);}
    }
    public synchronized String note(String day){return notes.optString(day,"");}
    public synchronized void saveNote(String day,String text){LocalDate.parse(day);emit("note",day,object("text",text));}
    public synchronized void recordFocus(int seconds){if(seconds>0)emit("focus",UUID.randomUUID().toString(),object("seconds",seconds,"task_uid",null,"created_at",LocalDateTime.now().withNano(0).toString()));}
    public synchronized JSONArray outgoing(){JSONArray result=new JSONArray();int after=prefs.getInt("push",0);for(int i=after;i<events.length()&&result.length()<500;i++)result.put(events.opt(i));return result;}
    public synchronized long cursor(){return prefs.getLong("pull",0);}
    public synchronized void merge(JSONArray incoming,long cursor,int pushed){for(int i=0;i<incoming.length();i++)apply(incoming.optJSONObject(i));persist();prefs.edit().putLong("pull",cursor).putInt("push",pushed).commit();}
    public synchronized String export(){return object("tasks",tasks,"notes",notes,"focus",focus,"note_versions",versions).toString();}
    public SharedPreferences preferences(){return prefs;}
    public synchronized void pairing(String endpoint,String fingerprint,String token) {
        try {
            KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);String alias="genta-pairing";
            if(!store.containsAlias(alias)) {KeyGenerator gen=KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES,"AndroidKeyStore");gen.init(new KeyGenParameterSpec.Builder(alias,KeyProperties.PURPOSE_ENCRYPT|KeyProperties.PURPOSE_DECRYPT).setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());gen.generateKey();}
            SecretKey key=(SecretKey)store.getKey(alias,null);Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.ENCRYPT_MODE,key);
            String encrypted=Base64.encodeToString(cipher.doFinal(token.getBytes(StandardCharsets.UTF_8)),Base64.NO_WRAP),iv=Base64.encodeToString(cipher.getIV(),Base64.NO_WRAP);
            prefs.edit().putString("endpoint",endpoint).putString("fingerprint",fingerprint).putString("token",encrypted).putString("iv",iv).putInt("push",0).putLong("pull",0).commit();
        }catch(Exception e){throw new IllegalStateException("Could not protect the pairing token",e);}
    }
    public synchronized String token() {
        try {KeyStore store=KeyStore.getInstance("AndroidKeyStore");store.load(null);SecretKey key=(SecretKey)store.getKey("genta-pairing",null);Cipher cipher=Cipher.getInstance("AES/GCM/NoPadding");cipher.init(Cipher.DECRYPT_MODE,key,new GCMParameterSpec(128,Base64.decode(prefs.getString("iv",""),Base64.NO_WRAP)));return new String(cipher.doFinal(Base64.decode(prefs.getString("token",""),Base64.NO_WRAP)),StandardCharsets.UTF_8);}catch(Exception e){throw new IllegalStateException("Pair this device again",e);}
    }
}
