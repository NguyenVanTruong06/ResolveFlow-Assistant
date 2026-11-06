# Codex review — G03-03A3-I

CHƯA NGHIỆM THU. Codex84focusedtestsPASS1.32s exit0.
Hai probe tmp/fakeopener:
1. Resp.status=None, no code/getcode, audio/wavvalid -> budgetoptindownload thànhcông. Packstatus phảiint200, missingkhôngđượcpass.
2. Drivefolder listing a?.wav vàa*.wav, fakefile1first/file2second -> draftchỉ1asset a_.wav chứa second. Prevalidation rawnamekhôngkhớpdownloader filename sanitize vàtargetremoveoverwrite.
Source pathvalidation resolveTRƯỚC ancestor is_symlink làm mất dấu symlink trỏ vào cùngstaging; chưarõđãrejecttheoguardđượccamkết.
Preflightcheck_declared raise trướctry/finallyresp.close ởpayloadloop -> cầntestresourceclosureerrorbranchesđãgiao.
Giao03A3-R1 sửastrictstatus/finalpathcollision/nofollow/sourcecompat/resourceclose, khôngfeaturemới.
