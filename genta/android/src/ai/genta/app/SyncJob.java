package ai.genta.app;
import android.app.job.*;
public final class SyncJob extends JobService {
    private Thread worker;
    @Override public boolean onStartJob(JobParameters params){
        worker=new Thread(()->{
            boolean retry=false;
            try{if(!Repository.get(this).preferences().getString("endpoint","").isEmpty())Sync.run(this);}
            catch(Exception error){Repository.get(this).preferences().edit().putString("last_sync_error",error.getClass().getSimpleName()+": "+error.getMessage()).apply();retry=true;}
            jobFinished(params,retry);
        },"GentaSync");worker.start();return true;
    }
    @Override public boolean onStopJob(JobParameters params){if(worker!=null)worker.interrupt();return true;}
}
