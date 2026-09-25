"""VN100 constituents used by the database-only /scan command.

The list was verified from Vietcap's ``priceboard/tickers/price/group`` endpoint
with group ``VN100`` on 2026-09-25. Keep this module isolated so a quarterly
constituent refresh changes one auditable source only.
"""

VN100_SYMBOLS = frozenset(
    """
    ACB ANV BAF BCM BID BMP BSI BSR BVH BWE CII CMG CTD CTG CTR CTS DBC DCM
    DGW DIG DPM DSE DXG EIB EVF FPT FRT FTS GAS GEE GEX GMD GVR HAG HCM HDB
    HDG HHV HPG HSG HT1 KBC KDC KDH KOS LPB MBB MCH MSB MSN MWG NAB NKG NLG
    NT2 NVL OCB PAN PC1 PDR PHR PLX PNJ POW PVD PVT REE SAB SBT SHB SIP SJS
    SSB SSI STB TAL TCB TCH TCX TPB VCB VCG VCI VCK VGC VHC VHM VIB VIC VIX
    VJC VND VNM VPB VPI VPL VPX VRE VSC VTP
    """.split()
)

if len(VN100_SYMBOLS) != 100:  # Fail fast if the maintained list is malformed.
    raise RuntimeError("Danh sách VN100 phải có đúng 100 mã")
