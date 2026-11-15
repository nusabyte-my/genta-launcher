package ai.genta.app;

import android.Manifest;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.graphics.*;
import android.media.*;
import android.media.session.*;
import android.media.session.MediaController;
import android.net.Uri;
import android.os.*;
import android.provider.Settings;
import android.speech.RecognizerIntent;
import android.view.*;
import android.widget.*;
import org.json.*;
import java.io.*;
import java.time.*;
import java.util.*;
import java.util.concurrent.*;

public final class MainActivity extends Activity {
    private final int BG=Color.rgb(16,16,16),FG=Color.rgb(239,237,231),RED=Color.rgb(232,74,60),MUTED=Color.rgb(160,160,156);
    private Repository repo;
    private LinearLayout root,body;
    private String page="Today",filter="today";
    private final Handler handler=new Handler(Looper.getMainLooper());
    private final ExecutorService executor=Executors.newSingleThreadExecutor();
    private boolean syncing=false;
    private EditText noteEditor;
    private String noteDay=LocalDate.now().toString();
    private final Runnable noteSave=()->saveNote();
    private MediaPlayer ambience;
    private JSONObject translations=new JSONObject();
    private TextView timerView;
    private final Runnable pulse=new Runnable(){public void run(){if(page.equals("Focus"))updateTimer();handler.postDelayed(this,1000);}};
    private LinearLayout musicBody;
    private boolean resumed=false,musicWatching=false;
    private MediaSessionManager musicManager;
    private MediaController activeMusic;
    private int musicPlayback=-1;
    private long musicActions=-1;
    private final MediaSessionManager.OnActiveSessionsChangedListener musicSessions=controllers->refreshMusic(false);
    private final MediaController.Callback musicCallback=new MediaController.Callback(){
        @Override public void onMetadataChanged(MediaMetadata data){refreshMusic(true);}
        @Override public void onPlaybackStateChanged(PlaybackState state){
            int playback=state==null?-1:state.getState();long actions=state==null?0:state.getActions();
            if(playback!=musicPlayback||actions!=musicActions)refreshMusic(true);
        }
        @Override public void onSessionDestroyed(){refreshMusic(true);}
    };
    private String export;
    private TextView status;

    @Override public void onCreate(Bundle state){
        super.onCreate(state);repo=Repository.get(this);getWindow().setStatusBarColor(BG);getWindow().setNavigationBarColor(BG);getWindow().getDecorView().setSystemUiVisibility(0);loadLanguage();Reminders.channel(this);Sync.schedule(this);render();handleIntent(getIntent());handler.post(pulse);
    }
    @Override protected void onNewIntent(Intent intent){super.onNewIntent(intent);setIntent(intent);handleIntent(intent);}
    private void handleIntent(Intent intent){
        String view=intent.getStringExtra("view");
        if(Arrays.asList("today","inbox","all").contains(view)){filter=view;page="inbox".equals(view)?"Inbox":"Today";render();}
        if(Intent.ACTION_SEND.equals(intent.getAction())&&"text/plain".equals(intent.getType())){String text=intent.getStringExtra(Intent.EXTRA_TEXT);if(text!=null)compose(text);}
        Uri uri=intent.getData();if(uri!=null&&"genta".equals(uri.getScheme())&&"add".equals(uri.getHost()))compose(uri.getQueryParameter("text"));
        if("voice".equals(intent.getStringExtra("action")))voice();if("focus".equals(intent.getStringExtra("action"))){page="Focus";render();}
        if("new_task".equals(intent.getStringExtra("action")))compose("");
    }
    private void loadLanguage(){String code=repo.preferences().getString("language","en");getWindow().getDecorView().setLayoutDirection("ar".equals(code)?View.LAYOUT_DIRECTION_RTL:View.LAYOUT_DIRECTION_LTR);try(InputStream stream=getAssets().open("locales/"+code+".json")){translations=new JSONObject(read(stream));}catch(Exception error){translations=new JSONObject();}}
    private static String read(InputStream in)throws IOException{ByteArrayOutputStream result=new ByteArrayOutputStream();byte[] b=new byte[8192];int n;while((n=in.read(b))!=-1)result.write(b,0,n);return result.toString("UTF-8");}
    private String tr(String value){return translations.optString(value,value);}
    private int dp(int n){return (int)(getResources().getDisplayMetrics().density*n+.5f);}
    private TextView rawText(String value,int size,int color){TextView view=new TextView(this);view.setText(value);view.setTextColor(color);view.setTextSize(size);view.setPadding(0,dp(5),0,dp(5));return view;}
    private TextView text(String value,int size,int color){return rawText(tr(value),size,color);}
    private Button button(String title,Runnable action){Button b=new Button(this);b.setText(tr(title));b.setTextColor(FG);b.setBackgroundTintList(android.content.res.ColorStateList.valueOf(Color.rgb(39,39,39)));b.setAllCaps(false);b.setOnClickListener(v->{try{action.run();}catch(Exception error){error(error);}});return b;}
    private EditText input(String hint,String value){EditText view=new EditText(this);view.setTextColor(FG);view.setHintTextColor(MUTED);view.setTextSize(15);view.setHint(tr(hint));view.setText(value);view.setBackgroundTintList(android.content.res.ColorStateList.valueOf(MUTED));return view;}
    private void error(Exception error){new AlertDialog.Builder(this).setTitle(tr("Genta")).setMessage(error.getMessage()==null?error.getClass().getSimpleName():error.getMessage()).setPositiveButton(tr("OK"),null).show();}
    private void message(String value){Toast.makeText(this,tr(value),Toast.LENGTH_LONG).show();}
    private void render(){
        if(!page.equals("Focus"))stopAmbience();
        if(!page.equals("Music")){stopMusicWatch();musicBody=null;}
        saveNote();noteEditor=null;root=new LinearLayout(this);root.setOrientation(LinearLayout.VERTICAL);root.setPadding(dp(18),dp(12),dp(18),dp(10));root.setBackgroundColor(BG);
        root.setOnApplyWindowInsetsListener((view,insets)->{
            if(Build.VERSION.SDK_INT>=30){android.graphics.Insets safe=insets.getInsets(WindowInsets.Type.systemBars());view.setPadding(dp(18)+safe.left,dp(12)+safe.top,dp(18)+safe.right,dp(10)+safe.bottom);}
            else view.setPadding(dp(18)+insets.getSystemWindowInsetLeft(),dp(12)+insets.getSystemWindowInsetTop(),dp(18)+insets.getSystemWindowInsetRight(),dp(10)+insets.getSystemWindowInsetBottom());
            return insets;
        });
        DotHeading heading=new DotHeading(this);root.addView(heading,new LinearLayout.LayoutParams(-1,dp(50)));
        root.addView(text("A SMALL SPACE FOR WHAT MATTERS",10,MUTED));
        HorizontalScrollView nav=new HorizontalScrollView(this);nav.setHorizontalScrollBarEnabled(false);LinearLayout tabs=new LinearLayout(this);
        for(String name:Arrays.asList("Today","Inbox","Notes","Focus","Music","Settings")){Button tab=button(name,()->{page=name;if(name.equals("Today"))filter="today";if(name.equals("Inbox"))filter="inbox";render();});if(name.equals(page)){tab.setTextColor(BG);tab.setBackgroundTintList(android.content.res.ColorStateList.valueOf(FG));nav.post(()->nav.scrollTo(Math.max(0,tab.getLeft()-dp(8)),0));}tabs.addView(tab);}
        nav.addView(tabs);root.addView(nav);
        ScrollView scroll=new ScrollView(this);body=new LinearLayout(this);body.setOrientation(LinearLayout.VERTICAL);body.setPadding(0,dp(12),0,dp(12));scroll.addView(body);root.addView(scroll,new LinearLayout.LayoutParams(-1,0,1));
        status=text("LOCAL FIRST  /  WINDOWS · LINUX · ANDROID",10,MUTED);root.addView(status);setContentView(root);
        if(page.equals("Today")||page.equals("Inbox"))tasks();else if(page.equals("Notes"))notes();else if(page.equals("Focus"))focus();else if(page.equals("Music"))music();else settings();
    }
    private void tasks(){
        body.addView(text(page.equals("Inbox")?"Room for later.":"Make room for what matters.",24,FG));
        body.addView(button("+ Add task",()->compose("")));body.addView(button("Speak a task",this::voice));body.addView(button("Sync now",this::sync));
        String[] choices={tr("Today + overdue"),tr("Inbox"),tr("All tasks")};Spinner mode=new Spinner(this);mode.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,choices));mode.setSelection(filter.equals("today")?0:filter.equals("inbox")?1:2,false);
        mode.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener(){public void onNothingSelected(android.widget.AdapterView<?> p){}public void onItemSelected(android.widget.AdapterView<?> p,View v,int pos,long id){String next=new String[]{"today","inbox","all"}[pos];if(!next.equals(filter)){filter=next;page=next.equals("inbox")?"Inbox":"Today";render();}}});body.addView(mode);
        int count=0;
        for(JSONObject task:repo.tasks(filter)){
            LinearLayout card=new LinearLayout(this);card.setOrientation(LinearLayout.VERTICAL);card.setPadding(dp(10),dp(8),dp(10),dp(8));card.setBackgroundColor(Color.rgb(27,27,27));
            CheckBox done=new CheckBox(this);done.setText(task.optString("title"));done.setTextColor(FG);done.setButtonTintList(android.content.res.ColorStateList.valueOf(RED));done.setChecked(task.optInt("done")==1);String uid=task.optString("uid");
            done.setOnCheckedChangeListener((b,value)->{repo.update(uid,Repository.object("done",value?1:0));Reminders.schedule(this);Widget.refresh(this);render();});card.addView(done);
            String detail=(task.isNull("day")?tr("Inbox"):task.optString("day"))+(task.isNull("due_at")?"":"  ·  "+task.optString("due_at").replace('T',' '));
            if(!task.optString("project").isEmpty())detail+="  ·  "+task.optString("project");if(!task.isNull("deadline"))detail+="  ·  "+tr("Deadline")+" "+task.optString("deadline");if(!task.optString("repeat_rule").equals("none"))detail+="  ·  "+task.optString("repeat_rule");
            card.addView(text(detail,12,MUTED));card.addView(button("Edit / schedule",()->editTask(task)));body.addView(card,new LinearLayout.LayoutParams(-1,-2));count++;
        }
        if(count==0)body.addView(text("Nothing here yet. Add your first small step.",15,MUTED));
    }
    private void compose(String initial){
        EditText edit=input("Task — try ‘Call Sam tomorrow at 5pm’",initial==null?"":initial);
        new AlertDialog.Builder(this).setTitle(tr("New task")).setView(edit).setNegativeButton(tr("Cancel"),null).setPositiveButton(tr("Add"),(d,w)->{try{repo.add(edit.getText().toString(),filter.equals("inbox")?null:LocalDate.now().toString());Reminders.schedule(this);Widget.refresh(this);render();}catch(Exception error){error(error);}}).show();
    }
    private void editTask(JSONObject task){
        LinearLayout form=new LinearLayout(this);form.setOrientation(LinearLayout.VERTICAL);form.setPadding(dp(16),0,dp(16),0);
        EditText title=input("Task",task.optString("title")),day=input("Day YYYY-MM-DD; blank for Inbox",task.isNull("day")?"":task.optString("day")),due=input("Reminder YYYY-MM-DDTHH:MM; blank for none",task.isNull("due_at")?"":task.optString("due_at")),deadline=input("Deadline YYYY-MM-DD; blank for none",task.isNull("deadline")?"":task.optString("deadline")),project=input("Project",task.optString("project")),minutes=input("Estimated minutes",String.valueOf(task.optInt("estimate_minutes",25)));
        for(EditText view:Arrays.asList(title,day,due,deadline,project,minutes))form.addView(view);
        Spinner repeat=new Spinner(this);String[] rules={"none","daily","weekdays","weekly","monthly"};repeat.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,rules));repeat.setSelection(Arrays.asList(rules).indexOf(task.optString("repeat_rule","none")));form.addView(repeat);
        new AlertDialog.Builder(this).setTitle(tr("Edit task")).setView(form).setNegativeButton(tr("Cancel"),null).setNeutralButton(tr("Delete"),(d,w)->{repo.update(task.optString("uid"),Repository.object("deleted",1));Reminders.schedule(this);Widget.refresh(this);render();}).setPositiveButton(tr("Save"),(d,w)->{
            try{
                String dayText=day.getText().toString().trim(),dueText=due.getText().toString().trim(),deadlineText=deadline.getText().toString().trim(),titleText=title.getText().toString().trim();if(titleText.isEmpty())throw new IllegalArgumentException("A task needs a title");
                if(!dayText.isEmpty())LocalDate.parse(dayText);if(!deadlineText.isEmpty())LocalDate.parse(deadlineText);if(!dueText.isEmpty())dayText=LocalDateTime.parse(dueText).toLocalDate().toString();int estimate=Integer.parseInt(minutes.getText().toString());if(estimate<1||estimate>480)throw new IllegalArgumentException("Duration must be 1–480 minutes");
                repo.update(task.optString("uid"),Repository.object("title",titleText,"day",dayText.isEmpty()?null:dayText,"due_at",dueText.isEmpty()?null:dueText,"deadline",deadlineText.isEmpty()?null:deadlineText,"project",project.getText().toString().trim(),"repeat_rule",rules[repeat.getSelectedItemPosition()],"estimate_minutes",estimate));Reminders.schedule(this);Widget.refresh(this);render();
            }catch(Exception error){error(error);}
        }).show();
    }
    private void voice(){
        Intent intent=new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL,RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);intent.putExtra(RecognizerIntent.EXTRA_PROMPT,tr("Speak a task"));
        try{startActivityForResult(intent,80);}catch(ActivityNotFoundException error){message("Install a speech recognition service on Android first.");}
    }
    private void notes(){
        body.addView(text("A place for the loose ends.",24,FG));
        EditText day=input("Daily note date",noteDay);day.setSingleLine(true);body.addView(day);body.addView(button("Open day",()->{LocalDate.parse(day.getText().toString());saveNote();noteDay=day.getText().toString();render();}));
        noteEditor=input("A thought, an idea, something to return to…",repo.note(noteDay));noteEditor.setGravity(Gravity.TOP);noteEditor.setMinLines(10);body.addView(noteEditor);
        noteEditor.addTextChangedListener(new android.text.TextWatcher(){public void beforeTextChanged(CharSequence s,int start,int count,int after){}public void onTextChanged(CharSequence s,int start,int before,int count){handler.removeCallbacks(noteSave);handler.postDelayed(noteSave,450);}public void afterTextChanged(android.text.Editable e){}});
        body.addView(button("Selected text → task",()->{int start=noteEditor.getSelectionStart(),end=noteEditor.getSelectionEnd();String all=noteEditor.getText().toString();String selected=start!=end?all.substring(Math.min(start,end),Math.max(start,end)):all.substring(all.lastIndexOf('\n',Math.max(0,start-1))+1,all.indexOf('\n',start)<0?all.length():all.indexOf('\n',start));repo.add(selected,noteDay);Widget.refresh(this);message("Task created. Original note kept.");}));
        body.addView(text("Notes save automatically on this device.",12,MUTED));
    }
    private void saveNote(){if(noteEditor!=null){String value=noteEditor.getText().toString();if(!value.equals(repo.note(noteDay)))repo.saveNote(noteDay,value);}}
    private void focus(){
        body.addView(text("One thing at a time.",24,FG));timerView=text("25:00",56,FG);timerView.setTypeface(Typeface.MONOSPACE);timerView.setGravity(Gravity.CENTER);body.addView(timerView);
        EditText minutes=input("Minutes; 0 for stopwatch",String.valueOf(repo.preferences().getInt("focus_duration",1500)/60));minutes.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);body.addView(minutes);
        body.addView(button("Start / pause",()->{
            boolean running=repo.preferences().getBoolean("focus_run",false);long elapsed=elapsedFocus();
            if(running){repo.preferences().edit().putLong("focus_elapsed",elapsed).putBoolean("focus_run",false).commit();cancelFocusAlarm();}
            else{int duration=Integer.parseInt(minutes.getText().toString())*60;if(duration<0||duration>10800)throw new IllegalArgumentException("Choose up to 180 minutes");repo.preferences().edit().putInt("focus_duration",duration).putLong("focus_started",SystemClock.elapsedRealtime()).putBoolean("focus_run",true).commit();scheduleFocusAlarm();}
            updateTimer();
        }));
        body.addView(button("+5 minutes",()->{int duration=repo.preferences().getInt("focus_duration",1500);if(duration>0){repo.preferences().edit().putInt("focus_duration",duration+300).commit();scheduleFocusAlarm();updateTimer();}}));
        body.addView(button("Finish session",this::finishFocus));
        Spinner sounds=new Spinner(this);String[] names={"Silence","Brown noise","Pink noise","Rain","Ocean"};sounds.setAdapter(new ArrayAdapter<String>(this,android.R.layout.simple_spinner_dropdown_item,names));sounds.setSelection(repo.preferences().getInt("sound",0),false);sounds.setOnItemSelectedListener(new android.widget.AdapterView.OnItemSelectedListener(){public void onNothingSelected(android.widget.AdapterView<?> p){}public void onItemSelected(android.widget.AdapterView<?> p,View v,int position,long id){repo.preferences().edit().putInt("sound",position).apply();playAmbience();}});body.addView(sounds);body.addView(text("Original synthesized soundscapes. Audio plays while this screen is active.",12,MUTED));updateTimer();
    }
    private long elapsedFocus(){long elapsed=repo.preferences().getLong("focus_elapsed",0);if(repo.preferences().getBoolean("focus_run",false)){long started=repo.preferences().getLong("focus_started",SystemClock.elapsedRealtime());long delta=SystemClock.elapsedRealtime()-started;if(delta>0)elapsed+=delta;}return elapsed;}
    private void updateTimer(){
        int duration=repo.preferences().getInt("focus_duration",1500);long elapsed=elapsedFocus()/1000;
        if(duration>0&&elapsed>=duration&&repo.preferences().getBoolean("focus_run",false)){finishFocus();return;}
        long value=duration>0?Math.max(0,duration-elapsed):elapsed;if(timerView!=null)timerView.setText(String.format(Locale.ROOT,"%02d:%02d",value/60,value%60));
        boolean running=repo.preferences().getBoolean("focus_run",false);if(!running&&ambience!=null&&ambience.isPlaying())ambience.pause();if(running){if(ambience==null)playAmbience();else if(!ambience.isPlaying())ambience.start();}
    }
    private void finishFocus(){int duration=repo.preferences().getInt("focus_duration",1500);int seconds=(int)(elapsedFocus()/1000);if(duration>0)seconds=Math.min(seconds,duration);repo.preferences().edit().putBoolean("focus_run",false).putLong("focus_elapsed",0).commit();cancelFocusAlarm();repo.recordFocus(seconds);stopAmbience();Widget.refresh(this);if(page.equals("Focus"))render();message("Session saved.");}
    private PendingIntent focusPending(){return PendingIntent.getBroadcast(this,8742,new Intent(this,Reminders.class).setAction("focus_complete"),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);}
    private void cancelFocusAlarm(){((AlarmManager)getSystemService(ALARM_SERVICE)).cancel(focusPending());}
    private void scheduleFocusAlarm(){int duration=repo.preferences().getInt("focus_duration",1500);if(duration==0)return;long when=System.currentTimeMillis()+Math.max(1000,duration*1000L-elapsedFocus());AlarmManager alarm=(AlarmManager)getSystemService(ALARM_SERVICE);if(Build.VERSION.SDK_INT<31||alarm.canScheduleExactAlarms())alarm.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP,when,focusPending());else alarm.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP,when,focusPending());}
    private void playAmbience(){stopAmbience();int choice=repo.preferences().getInt("sound",0);if(choice==0||!page.equals("Focus")||!repo.preferences().getBoolean("focus_run",false))return;String name=new String[]{"","brown-noise","pink-noise","rain","ocean"}[choice];try{android.content.res.AssetFileDescriptor file=getAssets().openFd("sounds/"+name+".wav");ambience=new MediaPlayer();ambience.setDataSource(file.getFileDescriptor(),file.getStartOffset(),file.getLength());file.close();ambience.setLooping(true);ambience.setVolume(.09f,.09f);ambience.prepare();ambience.start();}catch(IOException error){stopAmbience();message("Focus sound could not be loaded.");}}
    private void stopAmbience(){if(ambience!=null){ambience.release();ambience=null;}}
    private void music(){
        body.addView(text("SimpMusic, within reach.",24,FG));musicBody=new LinearLayout(this);musicBody.setOrientation(LinearLayout.VERTICAL);body.addView(musicBody);
        body.addView(button("Open SimpMusic",()->{Intent launch=getPackageManager().getLaunchIntentForPackage("com.maxrave.simpmusic");if(launch==null)throw new IllegalArgumentException("Install SimpMusic for Android first");startActivity(launch);}));
        body.addView(button("Allow media controls",()->startActivity(new Intent(Settings.ACTION_NOTIFICATION_LISTENER_SETTINGS))));watchMusic();
    }
    private void watchMusic(){
        if(!resumed)return;
        musicManager=(MediaSessionManager)getSystemService(MEDIA_SESSION_SERVICE);
        try{
            if(!musicWatching){musicManager.addOnActiveSessionsChangedListener(musicSessions,new ComponentName(this,MusicAccess.class),handler);musicWatching=true;}
            refreshMusic(true);
        }catch(SecurityException error){stopMusicWatch();musicReason(tr("Media controls are off. Android requires explicit notification-access permission."));}
    }
    private void stopMusicWatch(){
        if(activeMusic!=null){activeMusic.unregisterCallback(musicCallback);activeMusic=null;}
        if(musicWatching){
            try{musicManager.removeOnActiveSessionsChangedListener(musicSessions);}catch(SecurityException revoked){/* Access can be revoked in system settings. */}
            musicWatching=false;
        }
        musicPlayback=-1;musicActions=-1;
    }
    private void musicReason(String reason){if(musicBody!=null){musicBody.removeAllViews();musicBody.addView(rawText(reason,15,MUTED));}}
    private void refreshMusic(boolean force){
        if(!resumed||!page.equals("Music")||musicBody==null)return;
        try{
            MediaController chosen=null;
            for(MediaController controller:musicManager.getActiveSessions(new ComponentName(this,MusicAccess.class)))if(controller.getPackageName().equals("com.maxrave.simpmusic")){chosen=controller;break;}
            if(chosen==null){boolean hadSession=activeMusic!=null;if(activeMusic!=null){activeMusic.unregisterCallback(musicCallback);activeMusic=null;}if(force||hadSession||musicBody.getChildCount()==0)musicReason(tr("Open SimpMusic and play a track to connect."));return;}
            boolean changed=activeMusic==null||!activeMusic.getSessionToken().equals(chosen.getSessionToken());
            if(changed){if(activeMusic!=null)activeMusic.unregisterCallback(musicCallback);activeMusic=chosen;activeMusic.registerCallback(musicCallback,handler);}
            if(!force&&!changed&&musicBody.getChildCount()>0)return;
            final MediaController controller=activeMusic;MediaMetadata data=controller.getMetadata();PlaybackState state=controller.getPlaybackState();
            musicPlayback=state==null?-1:state.getState();musicActions=state==null?0:state.getActions();musicBody.removeAllViews();
            if(data!=null){
                Bitmap art=data.getBitmap(MediaMetadata.METADATA_KEY_ALBUM_ART);if(art==null)art=data.getBitmap(MediaMetadata.METADATA_KEY_ART);
                if(art!=null){ImageView image=new ImageView(this);image.setImageBitmap(art);musicBody.addView(image,new LinearLayout.LayoutParams(dp(120),dp(120)));}
                musicBody.addView(rawText(data.getString(MediaMetadata.METADATA_KEY_TITLE),20,FG));musicBody.addView(rawText(data.getString(MediaMetadata.METADATA_KEY_ARTIST),15,MUTED));
                String album=data.getString(MediaMetadata.METADATA_KEY_ALBUM);if(album!=null&&!album.isEmpty())musicBody.addView(rawText(album,13,MUTED));
            }
            Button previous=button("Previous",()->controller.getTransportControls().skipToPrevious());previous.setEnabled((musicActions&PlaybackState.ACTION_SKIP_TO_PREVIOUS)!=0);musicBody.addView(previous);
            boolean playing=musicPlayback==PlaybackState.STATE_PLAYING;
            Button toggle=button(playing?"Pause":"Play",()->{PlaybackState current=controller.getPlaybackState();if(current!=null&&current.getState()==PlaybackState.STATE_PLAYING)controller.getTransportControls().pause();else controller.getTransportControls().play();});
            toggle.setEnabled((musicActions&(PlaybackState.ACTION_PLAY_PAUSE|(playing?PlaybackState.ACTION_PAUSE:PlaybackState.ACTION_PLAY)))!=0);musicBody.addView(toggle);
            Button next=button("Next",()->controller.getTransportControls().skipToNext());next.setEnabled((musicActions&PlaybackState.ACTION_SKIP_TO_NEXT)!=0);musicBody.addView(next);
        }catch(SecurityException error){stopMusicWatch();musicReason(tr("Media controls are off. Android requires explicit notification-access permission."));}
    }
    private void settings(){
        body.addView(text("Windows · Linux · Android",24,FG));body.addView(button("Pair with desktop hub",this::pair));body.addView(button("Sync now",this::sync));
        body.addView(button("Add home widget",()->{
            android.appwidget.AppWidgetManager manager=android.appwidget.AppWidgetManager.getInstance(this);
            if(!manager.isRequestPinAppWidgetSupported())throw new IllegalStateException(tr("Use your launcher's widget picker on this device."));
            if(!manager.requestPinAppWidget(new ComponentName(this,Widget.class),null,null))message(tr("The launcher did not accept the widget request."));
        }));
        body.addView(text("Sync uses pinned HTTPS and a device-pairing token. Your token is encrypted with Android Keystore. The hub must be reachable; offline changes queue locally.",13,MUTED));
        body.addView(button("Enable reminders",()->{if(Build.VERSION.SDK_INT>=33)requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS},91);Reminders.schedule(this);}));
        body.addView(button("Allow precise reminder times",()->{if(Build.VERSION.SDK_INT>=31)startActivity(new Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,Uri.parse("package:"+getPackageName())));}));
        body.addView(text("Without exact-alarm access Android may deliver reminders later. Background synchronization runs approximately every 15 minutes, subject to Android battery policy.",13,MUTED));
        body.addView(button("Export local data",()->{saveNote();export=repo.export();Intent intent=new Intent(Intent.ACTION_CREATE_DOCUMENT).setType("application/json").addCategory(Intent.CATEGORY_OPENABLE).putExtra(Intent.EXTRA_TITLE,"genta-"+LocalDate.now()+".json");startActivityForResult(intent,82);}));
        body.addView(button("Language",()->{
            try{JSONObject languages=new JSONObject(read(getAssets().open("languages.json")));ArrayList<String> codes=new ArrayList<>(),names=new ArrayList<>();Iterator<String> keys=languages.keys();while(keys.hasNext()){String code=keys.next();codes.add(code);names.add(languages.getString(code));}new AlertDialog.Builder(this).setTitle(tr("Language")).setItems(names.toArray(new String[0]),(d,which)->{repo.preferences().edit().putString("language",codes.get(which)).commit();loadLanguage();Widget.refresh(this);render();}).show();}catch(Exception error){error(error);}
        }));
        String last=repo.preferences().getString("last_sync_error","");if(!last.isEmpty())body.addView(text(last,12,RED));
        body.addView(text("Genta 0.2 · Local-first planner. No advertising or telemetry. Speech recognition uses your chosen Android speech service.",12,MUTED));
    }
    private void pair(){EditText edit=input("Paste the desktop pairing JSON","");edit.setMinLines(5);new AlertDialog.Builder(this).setTitle(tr("Pair this device")).setView(edit).setNegativeButton(tr("Cancel"),null).setPositiveButton(tr("Pair"),(d,w)->{try{JSONObject data=new JSONObject(edit.getText().toString());Uri endpoint=Uri.parse(data.getString("endpoint"));if(!"https".equals(endpoint.getScheme())||endpoint.getHost()==null||endpoint.getUserInfo()!=null)throw new IllegalArgumentException("Use an HTTPS hub address");String fingerprint=data.getString("fingerprint");if(!fingerprint.matches("[0-9a-fA-F]{64}"))throw new IllegalArgumentException("Invalid certificate fingerprint");repo.pairing(data.getString("endpoint"),fingerprint,data.getString("token"));sync();}catch(Exception error){error(error);}}).show();}
    private void sync(){if(syncing)return;if(repo.preferences().getString("endpoint","").isEmpty()){pair();return;}syncing=true;if(status!=null)status.setText(tr("Synchronizing…"));executor.execute(()->{try{Sync.run(this);repo.preferences().edit().remove("last_sync_error").apply();runOnUiThread(()->{syncing=false;render();message("Synchronized.");});}catch(Exception error){repo.preferences().edit().putString("last_sync_error",error.getMessage()).apply();runOnUiThread(()->{syncing=false;error(error);});}});}
    @Override protected void onActivityResult(int request,int result,Intent data){super.onActivityResult(request,result,data);if(result!=RESULT_OK||data==null)return;if(request==80){ArrayList<String> values=data.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS);if(values!=null&&!values.isEmpty())compose(values.get(0));}if(request==82&&data.getData()!=null){try(OutputStream out=getContentResolver().openOutputStream(data.getData())){out.write(export.getBytes(java.nio.charset.StandardCharsets.UTF_8));message("Local data exported.");}catch(IOException error){error(error);}}}
    @Override protected void onResume(){super.onResume();resumed=true;if(repo!=null){Reminders.schedule(this);if(page.equals("Music"))watchMusic();if(!repo.preferences().getString("endpoint","").isEmpty())sync();}}
    @Override protected void onPause(){resumed=false;stopMusicWatch();saveNote();stopAmbience();super.onPause();}
    @Override protected void onDestroy(){stopMusicWatch();handler.removeCallbacksAndMessages(null);executor.shutdown();stopAmbience();super.onDestroy();}
    final class DotHeading extends View {
        Paint paint=new Paint(Paint.ANTI_ALIAS_FLAG);String[][] letters={{"11111","10000","10000","10111","10001","10001","01111"},{"11111","10000","10000","11110","10000","10000","11111"},{"10001","11001","10101","10011","10001","10001","10001"},{"11111","00100","00100","00100","00100","00100","00100"},{"01110","10001","10001","11111","10001","10001","10001"}};
        DotHeading(Context context){super(context);setContentDescription("Genta");}
        @Override protected void onDraw(Canvas canvas){super.onDraw(canvas);float step=dp(4),radius=dp(1);paint.setColor(FG);for(int i=0;i<5;i++)for(int y=0;y<7;y++)for(int x=0;x<5;x++)if(letters[i][y].charAt(x)=='1')canvas.drawCircle(dp(4)+(i*6+x)*step,dp(8)+y*step,radius,paint);paint.setColor(RED);canvas.drawCircle(dp(140),dp(31),dp(4),paint);}
    }
}
