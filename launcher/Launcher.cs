// Project-owned local launcher, GPL-3.0. No embedded third-party payloads.
using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows.Forms;
class LocalLauncher {
    static string Quote(string value) {
        var b=new StringBuilder("\""); int slash=0;
        foreach(char c in value) {
            if(c=='\\'){slash++;continue;}
            if(c=='"'){b.Append('\\',slash*2+1);b.Append(c);slash=0;continue;}
            b.Append('\\',slash);slash=0;b.Append(c);
        }
        b.Append('\\',slash*2);b.Append('"');return b.ToString();
    }
    [STAThread] static int Main(string[] args) {
        try {
            string root=AppDomain.CurrentDomain.BaseDirectory;
            string python=Path.Combine(root,"runtime","pythonw.exe");
            string entry=Path.Combine(root,"main.py");
            if(!File.Exists(python)||!File.Exists(entry))throw new FileNotFoundException("Local runtime or application source is missing.");
            var info=new ProcessStartInfo(python,"-I "+Quote(entry)+" "+String.Join(" ",args.Select(Quote)));
            info.WorkingDirectory=root;info.UseShellExecute=false;info.CreateNoWindow=true;
            using(var process=Process.Start(info)){process.WaitForExit();return process.ExitCode;}
        } catch(Exception e) {MessageBox.Show(e.Message,"StarScore",MessageBoxButtons.OK,MessageBoxIcon.Error);return 1;}
    }
}
