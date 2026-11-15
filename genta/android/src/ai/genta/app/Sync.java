package ai.genta.app;

import android.content.*;
import android.app.*;
import android.app.job.*;
import org.json.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.security.*;
import java.security.cert.*;
import javax.net.ssl.*;

public final class Sync {
    public static JSONObject request(Repository repo,JSONObject payload) throws Exception {
        String endpoint=repo.preferences().getString("endpoint","");URL url=new URL(endpoint+"/v1/sync");
        if(!url.getProtocol().equals("https")||url.getUserInfo()!=null)throw new IllegalArgumentException("Pair with an HTTPS Genta hub");
        final String expected=repo.preferences().getString("fingerprint","").replace(":","").toLowerCase(java.util.Locale.ROOT);
        if(!expected.matches("[0-9a-f]{64}"))throw new IllegalArgumentException("Invalid certificate fingerprint");
        TrustManager[] managers={new X509TrustManager(){
            public X509Certificate[] getAcceptedIssuers(){return new X509Certificate[0];}
            public void checkClientTrusted(X509Certificate[] chain,String auth)throws CertificateException{throw new CertificateException("Not a client verifier");}
            public void checkServerTrusted(X509Certificate[] chain,String auth)throws CertificateException{
                try {if(chain.length==0)throw new CertificateException("No peer certificate");byte[] digest=MessageDigest.getInstance("SHA-256").digest(chain[0].getEncoded());StringBuilder actual=new StringBuilder();for(byte b:digest)actual.append(String.format(java.util.Locale.ROOT,"%02x",b&255));if(!MessageDigest.isEqual(actual.toString().getBytes(StandardCharsets.US_ASCII),expected.getBytes(StandardCharsets.US_ASCII)))throw new CertificateException("Hub identity changed; re-pair before sending data");chain[0].checkValidity();}catch(GeneralSecurityException e){throw new CertificateException(e);}
            }
        }};
        SSLContext tls=SSLContext.getInstance("TLS");tls.init(null,managers,new SecureRandom());
        HttpsURLConnection conn=(HttpsURLConnection)url.openConnection();
        // The explicit certificate pin above authenticates this self-signed local hub.
        conn.setSSLSocketFactory(tls.getSocketFactory());conn.setHostnameVerifier((host,session)->host.equals(url.getHost()));
        conn.setConnectTimeout(10000);conn.setReadTimeout(20000);conn.setRequestMethod("POST");conn.setDoOutput(true);conn.setInstanceFollowRedirects(false);
        conn.setRequestProperty("Content-Type","application/json");conn.setRequestProperty("Authorization","Bearer "+repo.token());
        byte[] body=payload.toString().getBytes(StandardCharsets.UTF_8);conn.setFixedLengthStreamingMode(body.length);
        try {
            try(OutputStream out=conn.getOutputStream()){out.write(body);}
            int status=conn.getResponseCode();if(status!=200)throw new IOException("Hub refused synchronization (HTTP "+status+")");
            ByteArrayOutputStream data=new ByteArrayOutputStream();byte[] block=new byte[8192];
            try(InputStream in=conn.getInputStream()){int n;while((n=in.read(block))!=-1){data.write(block,0,n);if(data.size()>4*1024*1024)throw new IOException("Synchronization response too large");}}
            return new JSONObject(data.toString("UTF-8"));
        }finally{conn.disconnect();}
    }
    public static synchronized void run(Context context)throws Exception {
        Repository repo=Repository.get(context);
        for(int page=0;page<100;page++){
            JSONArray outgoing=repo.outgoing();int pushed=repo.preferences().getInt("push",0)+outgoing.length();
            JSONObject reply=request(repo,Repository.object("events",outgoing,"cursor",repo.cursor()));
            repo.merge(reply.getJSONArray("events"),reply.getLong("cursor"),pushed);
            if(!reply.optBoolean("more")&&outgoing.length()<500){Reminders.schedule(context);Widget.refresh(context);return;}
        }
        throw new IOException("Large sync history; synchronize again to continue");
    }
    public static void schedule(Context context){
        JobScheduler scheduler=(JobScheduler)context.getSystemService(Context.JOB_SCHEDULER_SERVICE);
        scheduler.schedule(new JobInfo.Builder(8471,new ComponentName(context,SyncJob.class)).setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY).setPeriodic(15*60*1000L).setPersisted(true).build());
    }
}
