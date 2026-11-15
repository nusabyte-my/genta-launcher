package ai.genta.app;

import android.app.*;
import android.content.*;
import android.os.Build;
import java.time.*;
import org.json.*;

public final class Reminders extends BroadcastReceiver {
    static final String CHANNEL="genta-reminders";
    public static void channel(Context context){
        NotificationManager manager=(NotificationManager)context.getSystemService(Context.NOTIFICATION_SERVICE);
        manager.createNotificationChannel(new NotificationChannel(CHANNEL,"Genta reminders",NotificationManager.IMPORTANCE_HIGH));
    }
    public static void schedule(Context context){
        channel(context);Repository repo=Repository.get(context);AlarmManager alarms=(AlarmManager)context.getSystemService(Context.ALARM_SERVICE);
        for(JSONObject task:repo.tasks("all")){
            String uid=task.optString("uid");PendingIntent pending=alarmIntent(context,uid);
            alarms.cancel(pending);
            if(task.optInt("done")==1||task.isNull("due_at"))continue;
            String due=task.optString("due_at");
            if(repo.preferences().getString("reminded:"+uid,"").equals(due))continue;
            try{
                long when=LocalDateTime.parse(due).atZone(ZoneId.systemDefault()).toInstant().toEpochMilli();when=Math.max(when,System.currentTimeMillis()+1000);
                if(Build.VERSION.SDK_INT<31||alarms.canScheduleExactAlarms())alarms.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP,when,pending);else alarms.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP,when,pending);
            }catch(java.time.format.DateTimeParseException ignored){/* Invalid remote dates are not scheduled. */}
        }
    }
    private static PendingIntent alarmIntent(Context context,String uid){
        Intent intent=new Intent(context,Reminders.class).setAction("remind").setData(android.net.Uri.parse("genta://reminder/"+uid)).putExtra("uid",uid);
        return PendingIntent.getBroadcast(context,uid.hashCode(),intent,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
    }
    private static PendingIntent action(Context context,String uid,String action){
        Intent intent=new Intent(context,Reminders.class).setAction(action).setData(android.net.Uri.parse("genta://"+action+"/"+uid)).putExtra("uid",uid);
        return PendingIntent.getBroadcast(context,(uid+action).hashCode(),intent,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
    }
    @Override public void onReceive(Context context,Intent intent){
        if(Intent.ACTION_BOOT_COMPLETED.equals(intent.getAction())){schedule(context);Sync.schedule(context);return;}
        if("focus_complete".equals(intent.getAction())){
            Repository data=Repository.get(context);
            if(!data.preferences().getBoolean("focus_run",false))return;
            int seconds=data.preferences().getInt("focus_duration",1500);
            data.preferences().edit().putBoolean("focus_run",false).putLong("focus_elapsed",0).commit();
            data.recordFocus(seconds);channel(context);
            PendingIntent open=PendingIntent.getActivity(context,2,new Intent(context,MainActivity.class).putExtra("action","focus"),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
            Notification notification=new Notification.Builder(context,CHANNEL).setSmallIcon(R.drawable.genta_icon).setContentTitle("Focus complete").setContentText("Your session is saved. Take a short break.").setContentIntent(open).setAutoCancel(true).build();
            try{((NotificationManager)context.getSystemService(Context.NOTIFICATION_SERVICE)).notify(8742,notification);}catch(SecurityException ignored){}
            Widget.refresh(context);return;
        }
        Repository repo=Repository.get(context);String uid=intent.getStringExtra("uid");JSONObject task=repo.get(uid);if(task==null)return;
        NotificationManager manager=(NotificationManager)context.getSystemService(Context.NOTIFICATION_SERVICE);
        if("done".equals(intent.getAction())){
            repo.update(uid,Repository.object("done",1));manager.cancel(uid.hashCode());schedule(context);Widget.refresh(context);return;
        }
        if("snooze".equals(intent.getAction())){
            LocalDateTime due=LocalDateTime.now().plusMinutes(5).withNano(0);repo.update(uid,Repository.object("day",due.toLocalDate().toString(),"due_at",due.toString()));manager.cancel(uid.hashCode());schedule(context);return;
        }
        if(task.optInt("done")==1||task.optInt("deleted")==1)return;
        channel(context);repo.preferences().edit().putString("reminded:"+uid,task.optString("due_at")).apply();
        PendingIntent open=PendingIntent.getActivity(context,0,new Intent(context,MainActivity.class),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        Notification notification=new Notification.Builder(context,CHANNEL).setSmallIcon(R.drawable.genta_icon).setContentTitle("Genta reminder").setContentText(task.optString("title")).setContentIntent(open).setAutoCancel(true).addAction(new Notification.Action.Builder(null,"Snooze 5 min",action(context,uid,"snooze")).build()).addAction(new Notification.Action.Builder(null,"Done",action(context,uid,"done")).build()).build();
        try{manager.notify(uid.hashCode(),notification);}catch(SecurityException ignored){/* The user may decline Android's notification permission. */}
    }
}
