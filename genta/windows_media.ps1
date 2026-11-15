param([ValidateSet('status','PlayPause','Next','Previous')][string]$Action='status')
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false)
Add-Type -AssemblyName System.Runtime.WindowsRuntime
$null=[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager,Windows.Media.Control,ContentType=WindowsRuntime]
$null=[Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties,Windows.Media.Control,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.IRandomAccessStreamWithContentType,Windows.Storage.Streams,ContentType=WindowsRuntime]
$null=[Windows.Storage.Streams.DataReader,Windows.Storage.Streams,ContentType=WindowsRuntime]
function Await-WinRT($Operation,[Type]$ResultType) {
    $Method=[System.WindowsRuntimeSystemExtensions].GetMethods() | Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetGenericArguments().Count -eq 1 -and $_.GetParameters().Count -eq 1 -and $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1' } | Select-Object -First 1
    $Task=$Method.MakeGenericMethod($ResultType).Invoke($null,@($Operation))
    if (-not $Task.Wait(5000)) { throw 'Windows media operation timed out.' }
    return $Task.Result
}
$Manager=Await-WinRT ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager]::RequestAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionManager])
$Session=$Manager.GetSessions() | Where-Object { $_.SourceAppUserModelId -match 'simpmusic' } | Select-Object -First 1
if (-not $Session) {
    @{available=$false;reason='SimpMusic is not exposing a Windows media session. Open it and play a track.'} | ConvertTo-Json -Compress
    exit 0
}
switch($Action) {
    'PlayPause' { $null=Await-WinRT ($Session.TryTogglePlayPauseAsync()) ([bool]) }
    'Next' { $null=Await-WinRT ($Session.TrySkipNextAsync()) ([bool]) }
    'Previous' { $null=Await-WinRT ($Session.TrySkipPreviousAsync()) ([bool]) }
}
$Properties=Await-WinRT ($Session.TryGetMediaPropertiesAsync()) ([Windows.Media.Control.GlobalSystemMediaTransportControlsSessionMediaProperties])
$Playback=$Session.GetPlaybackInfo()
$Artwork=''
if ($Properties.Thumbnail) {
    $Stream=Await-WinRT ($Properties.Thumbnail.OpenReadAsync()) ([Windows.Storage.Streams.IRandomAccessStreamWithContentType])
    try {
        if ($Stream.Size -gt 0 -and $Stream.Size -le 2097152) {
            $Reader=[Windows.Storage.Streams.DataReader]::new($Stream.GetInputStreamAt(0))
            try {
                $null=Await-WinRT ($Reader.LoadAsync([uint32]$Stream.Size)) ([uint32])
                $Bytes=New-Object byte[] ([int]$Stream.Size)
                $Reader.ReadBytes($Bytes)
                $Folder=Join-Path $env:LOCALAPPDATA 'Genta\cache'
                $null=New-Item -ItemType Directory -Force -Path $Folder
                $File=Join-Path $Folder 'simpmusic-artwork.bin'
                [IO.File]::WriteAllBytes($File,$Bytes)
                $Artwork=([Uri]$File).AbsoluteUri
            } finally { $Reader.Dispose() }
        }
    } finally { $Stream.Dispose() }
}
@{
    available=$true;title=$Properties.Title;artist=$Properties.Artist;album=$Properties.AlbumTitle
    status=$Playback.PlaybackStatus.ToString();art=$Artwork
    can_play=$Playback.Controls.IsPlayEnabled;can_pause=$Playback.Controls.IsPauseEnabled
    can_next=$Playback.Controls.IsNextEnabled;can_previous=$Playback.Controls.IsPreviousEnabled
} | ConvertTo-Json -Compress
