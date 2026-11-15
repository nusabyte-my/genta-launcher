package ai.genta.app;

import android.app.PendingIntent;
import android.appwidget.AppWidgetManager;
import android.appwidget.AppWidgetProvider;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.view.View;
import android.widget.RemoteViews;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.time.LocalDate;

public final class Widget extends AppWidgetProvider {
    public static void refresh(Context context){
        AppWidgetManager manager=AppWidgetManager.getInstance(context);
        int[] ids=manager.getAppWidgetIds(new ComponentName(context,Widget.class));
        if(ids.length>0)new Widget().onUpdate(context,manager,ids);
    }
    private static String tr(String value,JSONObject catalog){return catalog.optString(value,value);}
    private static JSONObject catalog(Context context,String language){
        try(InputStream in=context.getAssets().open("locales/"+language+".json")){
            ByteArrayOutputStream out=new ByteArrayOutputStream();byte[] buffer=new byte[4096];int count;
            while((count=in.read(buffer))!=-1)out.write(buffer,0,count);
            return new JSONObject(out.toString("UTF-8"));
        }catch(java.io.IOException|org.json.JSONException unavailable){return new JSONObject();}
    }
    @Override public void onUpdate(Context context,AppWidgetManager manager,int[] ids){
        Repository repo=Repository.get(context);String today=LocalDate.now().toString();
        String language=repo.preferences().getString("language","en");JSONObject catalog=catalog(context,language);
        int[] counts=new int[3];String[] first=new String[3];
        for(JSONObject task:repo.tasks("all")){
            int column;
            if(task.optInt("done")==1){if(!task.optString("completed_at").startsWith(today))continue;column=2;}
            else if(task.isNull("day"))column=0;
            else{if(task.optString("day").compareTo(today)>0)continue;column=1;}
            counts[column]++;if(first[column]==null)first[column]=task.optString("title");
        }
        int[] labels={R.id.widget_inbox_label,R.id.widget_planned_label,R.id.widget_done_label};
        int[] numbers={R.id.widget_inbox_count,R.id.widget_planned_count,R.id.widget_done_count};
        int[] tasks={R.id.widget_inbox_task,R.id.widget_planned_task,R.id.widget_done_task};
        String[] headings={tr("Inbox",catalog),tr("Planned",catalog),tr("Done",catalog)};
        Intent open=new Intent(context,MainActivity.class).putExtra("view","all");
        Intent add=new Intent(context,MainActivity.class).putExtra("view","inbox").putExtra("action","new_task");
        Intent voice=new Intent(context,MainActivity.class).putExtra("view","inbox").putExtra("action","voice");
        int flags=PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE;
        PendingIntent openAction=PendingIntent.getActivity(context,0,open,flags),addAction=PendingIntent.getActivity(context,1,add,flags),voiceAction=PendingIntent.getActivity(context,2,voice,flags);
        for(int id:ids){
            RemoteViews views=new RemoteViews(context.getPackageName(),R.layout.widget);
            views.setInt(R.id.widget_root,"setLayoutDirection","ar".equals(language)?View.LAYOUT_DIRECTION_RTL:View.LAYOUT_DIRECTION_LTR);
            views.setTextViewText(R.id.widget_title,"GENTA / "+tr("Today",catalog));
            for(int column=0;column<3;column++){
                views.setTextViewText(labels[column],headings[column]);views.setTextViewText(numbers[column],Integer.toString(counts[column]));
                views.setTextViewText(tasks[column],first[column]==null?tr("No tasks",catalog):first[column]);
            }
            views.setTextViewText(R.id.widget_add,tr("+ Task",catalog));views.setTextViewText(R.id.widget_voice,tr("Speak",catalog));views.setTextViewText(R.id.widget_open,tr("Open",catalog));
            views.setOnClickPendingIntent(R.id.widget_root,openAction);views.setOnClickPendingIntent(R.id.widget_open,openAction);views.setOnClickPendingIntent(R.id.widget_add,addAction);views.setOnClickPendingIntent(R.id.widget_voice,voiceAction);
            manager.updateAppWidget(id,views);
        }
    }
}
