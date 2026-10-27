$Password = "<TBD>" | ConvertTo-SecureString -AsPlainText -Force
$User = "root"
$Credential = New-Object System.Management.Automation.PSCredential ($User, $Password)

$db_hosts = @("X.X.X.X", "X.X.X.X", "X.X.X.X")
function Connect-DBCluster
{
  [CmdletBinding()]
  param (
    [Parameter()]
    [System.Object[]]
    $DbServers
  )

  foreach ($DbServer in $DbServers)
  {
    try
    {
      Open-MySqlConnection -ConnectionName $DbServer -Credential $Credential -Server $DbServer
    }
    catch
    {
      Write-Host "Cannot connect to $($DbServer)!! abort!!"
      Write-Host "Line - $($_.InvocationInfo.ScriptLineNumber) : An error occurred - $($_.Exception.Message)"
      return
    }
  }
}

function New-Databases
{
  [CmdletBinding()]
  param (
    [Parameter()]
    [System.Object[]]
    $DbServers
  )

  $i = 0
  foreach ($DbServer in $DbServers)
  {
    $i++
    try
    {
      $DatabaseSqlQueryCreate = "CREATE DATABASE IF NOT EXISTS Databasetest$($i);"
      Invoke-SqlQuery -ConnectionName $DbServer -Query $DatabaseSqlQueryCreate -WarningAction SilentlyContinue | Out-Null
    }
    catch
    {
      Write-Host "Cannot create Databasetest in $($DbServer)!! abort!!"
      Write-Host "Line - $($_.InvocationInfo.ScriptLineNumber) : An error occurred - $($_.Exception.Message)"
      return
    }
  }
}

function Remove-Databases
{
  [CmdletBinding()]
  param (
    [Parameter()]
    [System.Object[]]
    $DbServers
  )

  $i = 0
  foreach ($DbServer in $DbServers)
  {
    $i++
    try
    {
      $DatabaseSqlQueryDrop = "DROP DATABASE Databasetest$($i);"
      Invoke-SqlQuery -ConnectionName $DbServer -Query $DatabaseSqlQueryDrop -WarningAction SilentlyContinue | Out-Null
    }
    catch
    {
      Write-Host "Cannot Remove Databasetest in $($DbServer)!! abort!!"
      Write-Host "Line - $($_.InvocationInfo.ScriptLineNumber) : An error occurred - $($_.Exception.Message)"
      return
    }
  }
}

function Show-Databases
{
  [CmdletBinding()]
  param (
    [Parameter()]
    [System.Object[]]
    $DbServers
  )
  $i = 0
  $ArrayList = $null
  $ArrayList = New-Object System.Collections.ArrayList
  foreach ($DbServer in $DbServers)
  {
    $i++
    try
    {
      $Object = $null
      $Object = New-Object System.Object
      $Object | Add-Member -MemberType NoteProperty -Name "DbName" -Value $DbServer
      $Databases = $null
      $DatabaseSqlQueryShow = "SHOW DATABASES;"
      $Databases = Invoke-SqlQuery -ConnectionName $DbServer -Query $DatabaseSqlQueryShow -WarningAction SilentlyContinue
      if (($Databases | Where-Object { $_.Database -match "Databasetest" }).Database)
      {
        foreach ($Database in (($Databases | Where-Object { $_.Database -match "Databasetest" }).Database))
        {
          $Object | Add-Member -MemberType NoteProperty -Name $Database -Value $Database
        }
      }
      else
      {
        $Object | Add-Member -MemberType NoteProperty -Name "Message" -Value "NoDatabaseFound"
      }
      $ArrayList.Add($Object) | Out-Null
    }
    catch
    {
      Write-Host "Cannot get databases from $($DbServer)!! abort!!"
      Write-Host "Line - $($_.InvocationInfo.ScriptLineNumber) : An error occurred - $($_.Exception.Message)"
      return
    }
  }
  $ArrayList | Format-Table
}

Connect-DBCluster -DbServers $db_hosts
New-Databases -DbServers $db_hosts
Write-Host -ForegroundColor Cyan "Databases after create :"
Show-Databases -DbServers $db_hosts
Remove-Databases -DbServers $db_hosts
Write-Host -ForegroundColor Cyan "Databases after drop :"
Show-Databases -DbServers $db_hosts