function Green
{
    process {
        Write-Host $_ -ForegroundColor DarkMagenta
    }
}

function Red
{
    process {
        Write-Host $_ -ForegroundColor Red
    }
}

try
{
    dp load
}
catch
{
}
