"""
nesting_catena.py — ENOROSSI Paint Optimizer v5
Silhouette 2D per i 14 codici reali del catalogo ENOROSSI.
"""
from __future__ import annotations
import numpy as np, math, warnings
warnings.filterwarnings("ignore")
from dataclasses import dataclass, field
from typing import List, Optional
from shapely.geometry import Polygon, MultiPolygon
from shapely.ops import unary_union
from shapely.affinity import translate

COLORI_FAMIGLIA = {
    "Trinciatrici":"#0D47A1","Rotopresse":"#1B5E20","ENODUO":"#4A148C",
    "Falciatrici":"#E65100","Vendemmiatrici":"#880E4F","Traino":"#37474F",
    "Carter":"#4E342E","Carrello":"#006064","Bracci":"#F57F17",
    "Staffaggi":"#263238","Coperchi":"#558B2F",
}

@dataclass
class PezzoNesting:
    cod:str; nome:str; L_mm:float; H_mm:float; P_mm:float
    peso_kg:float; ganci_req:int; qty:int
    famiglia:str=""; colore:str=""; note:str=""
    shape:Optional[object]=field(default=None,repr=False)
    sw_mm:float=0.; sh_mm:float=0.
    def __post_init__(self):
        if not self.colore and self.famiglia:
            self.colore=COLORI_FAMIGLIA.get(self.famiglia,"#455A64")

CATALOGO_ENOROSSI:List[PezzoNesting]=[
    PezzoNesting("FALC-TF280","Trincia TF280",       2850, 980,700,185.,2,1,"Trinciatrici"),
    PezzoNesting("FALC-TF320","Trincia TF320",       3250,1000,720,220.,2,1,"Trinciatrici"),
    PezzoNesting("FALC-FN300","Falciatrice FN300",   3050, 460,420, 98.,2,1,"Falciatrici"),
    PezzoNesting("PRES-RB100","Rotopresse RB100",    2000,1600,1800,310.,3,1,"Rotopresse"),
    PezzoNesting("PRES-RB120","Rotopresse RB120",    2200,1700,1900,380.,3,1,"Rotopresse"),
    PezzoNesting("VEND-VN500","Vendemmiatrice VN500",4800,1600,2100,580.,5,1,"Vendemmiatrici"),
    PezzoNesting("ENOD-780",  "ENODUO 780",          3200,1400,1200,280.,3,1,"ENODUO"),
    PezzoNesting("BTRN-150",  "Barra Traino 150",    1550, 250,160, 38.,1,1,"Traino"),
    PezzoNesting("CART-LAT-M","Carter Laterale M",    680, 520,280, 12.,1,1,"Carter"),
    PezzoNesting("ROTA-STD",  "Ruota Standard",       220, 220,180,  8.5,1,1,"Carrello"),
    PezzoNesting("BRACC-TL-M","Braccio Telescopico",  980, 150,140,  5.8,1,1,"Bracci"),
    PezzoNesting("PIAS-AGG-M","Piastra Aggancio M",   280, 200, 25,  1.1,1,1,"Staffaggi"),
    PezzoNesting("STAF-UNI-S","Staffa Universale S",  250, 200, 80,  0.8,1,1,"Staffaggi"),
    PezzoNesting("COPR-VAL-S","Coperchio Valvola S",  280, 220, 60,  1.1,1,1,"Coperchi"),
]

def _ang(n=36): return np.linspace(0,2*math.pi,n)

def _sh_trincia(L,H):
    outer=Polygon([(0,H*.15),(L*.04,0),(L*.96,0),(L,H*.15),(L,H*.72),(L*.94,H),(L*.06,H),(0,H*.72)])
    ang=_ang(20)
    cx,cy,rx,ry=L/2,H*.08,L*.38,H*.18
    rotor=Polygon([(cx+rx*math.cos(math.pi-a),cy+ry*math.sin(a)) for a in ang]+[(L*.12,0),(L*.88,0)])
    el=Polygon([(0,H*.25),(L*.06,H*.20),(L*.06,H*.55),(0,H*.50)])
    er=Polygon([(L,H*.25),(L*.94,H*.20),(L*.94,H*.55),(L,H*.50)])
    u=unary_union([outer,rotor,el,er]); b=u.bounds; return translate(u,-b[0],-b[1])

def _sh_rotopresse(L,H):
    frame=Polygon([(0,0),(L,0),(L,H),(0,H)])
    ang=_ang(48); cx,cy=L/2,H*.52; r=min(L,H)*.38
    rullo=Polygon([(cx+r*math.cos(a),cy+r*math.sin(a)) for a in ang])
    foro =Polygon([(cx+r*.35*math.cos(a),cy+r*.35*math.sin(a)) for a in ang])
    fl=Polygon([(L*.08,0),(L*.28,0),(L*.28,H*.12),(L*.08,H*.12)])
    fr=Polygon([(L*.72,0),(L*.92,0),(L*.92,H*.12),(L*.72,H*.12)])
    sh=Polygon([(L*.1,H*.88),(L*.9,H*.88),(L*.9,H),(L*.1,H)])
    u=unary_union([frame,rullo,fl,fr,sh]).difference(foro); b=u.bounds; return translate(u,-b[0],-b[1])

def _sh_vendemmiatrice(L,H):
    body=Polygon([(L*.25,0),(L*.75,0),(L*.78,H*.15),(L*.82,H*.60),(L*.75,H),(L*.25,H),(L*.18,H*.60),(L*.22,H*.15)])
    wl=Polygon([(0,H*.20),(L*.26,H*.15),(L*.26,H*.65),(L*.05,H*.70),(0,H*.65)])
    wr=Polygon([(L,H*.20),(L*.74,H*.15),(L*.74,H*.65),(L*.95,H*.70),(L,H*.65)])
    tank=Polygon([(L*.30,H*.88),(L*.70,H*.88),(L*.72,H),(L*.28,H)])
    u=unary_union([body,wl,wr,tank]); b=u.bounds; return translate(u,-b[0],-b[1])

def _sh_enoduo(L,H):
    gw,gh=L*.055,H*.065
    lshape=Polygon([(0,0),(L,0),(L,gh*1.4),(gw,gh*1.4),(gw,H),(0,H)])
    diag=Polygon([(gw,gh*1.4),(gw+L*.30,gh*1.4),(gw+L*.30,gh*1.4+H*.08),(gw,H*.55),(0,H*.55),(0,H*.45),(gw*.5,H*.45)])
    top=Polygon([(0,H*.88),(gw*2.5,H*.88),(gw*2.5,H),(0,H)])
    u=unary_union([lshape,diag,top]); b=u.bounds; return translate(u,-b[0],-b[1])

def _sh_falciatrice(L,H):
    body=Polygon([(0,H*.30),(L*.08,H*.10),(L*.75,H*.10),(L*.80,H*.30),(L*.80,H*.70),(L*.75,H*.90),(L*.08,H*.90),(0,H*.70)])
    ang=_ang(36); cx,cy,r=L*.90,H*.50,H*.45
    disco=Polygon([(cx+r*math.cos(a),cy+r*math.sin(a)) for a in ang])
    a1=Polygon([(0,H*.15),(L*.06,H*.15),(L*.06,H*.40),(0,H*.40)])
    a2=Polygon([(0,H*.60),(L*.06,H*.60),(L*.06,H*.85),(0,H*.85)])
    u=unary_union([body,disco,a1,a2]); b=u.bounds; return translate(u,-b[0],-b[1])

def _sh_barra_traino(L,H):
    body=Polygon([(L*.08,H*.20),(L*.92,H*.20),(L*.92,H*.80),(L*.08,H*.80)])
    ang=_ang(24); r=H*.40
    for cx in [L*.05,L*.95]:
        oc=Polygon([(cx+r*math.cos(a),H*.5+r*math.sin(a)) for a in ang])
        fo=Polygon([(cx+r*.45*math.cos(a),H*.5+r*.45*math.sin(a)) for a in ang])
        body=unary_union([body,oc]).difference(fo)
    b=body.bounds; return translate(body,-b[0],-b[1])

def _sh_carter(L,H):
    vasca=Polygon([(0,H*.12),(L*.05,0),(L*.95,0),(L,H*.12),(L,H*.88),(L*.95,H),(L*.05,H),(0,H*.88)])
    ang=_ang(32); cx,cy,ra,rb=L/2,H/2,L*.28,H*.22
    foro=Polygon([(cx+ra*math.cos(a),cy+rb*math.sin(a)) for a in ang])
    n1=Polygon([(L*.15,H*.15),(L*.25,H*.15),(L*.25,H*.85),(L*.15,H*.85)])
    n2=Polygon([(L*.75,H*.15),(L*.85,H*.15),(L*.85,H*.85),(L*.75,H*.85)])
    u=unary_union([vasca,n1,n2]).difference(foro); b=u.bounds; return translate(u,-b[0],-b[1])

def _sh_ruota(L,H):
    ang=_ang(48); cx,cy=L/2,H/2; re=min(L,H)*.48; ri=re*.30; rh=re*.15
    cerchio=Polygon([(cx+re*math.cos(a),cy+re*math.sin(a)) for a in ang])
    anello =Polygon([(cx+ri*math.cos(a),cy+ri*math.sin(a)) for a in ang])
    mozzo  =Polygon([(cx+rh*math.cos(a),cy+rh*math.sin(a)) for a in ang])
    raggi=[]
    for ar in [0,math.pi/2,math.pi,3*math.pi/2]:
        w=re*.06; p=ar+math.pi/2
        x0,y0=cx+rh*math.cos(ar),cy+rh*math.sin(ar)
        x1,y1=cx+ri*math.cos(ar),cy+ri*math.sin(ar)
        raggi.append(Polygon([(x0+w*math.cos(p),y0+w*math.sin(p)),(x1+w*math.cos(p),y1+w*math.sin(p)),
                               (x1-w*math.cos(p),y1-w*math.sin(p)),(x0-w*math.cos(p),y0-w*math.sin(p))]))
    u=cerchio.difference(anello)
    u=unary_union([u,mozzo]+raggi); b=u.bounds; return translate(u,-b[0],-b[1])

def _sh_braccio(L,H):
    c1=Polygon([(L*.05,H*.25),(L*.55,H*.25),(L*.55,H*.75),(L*.05,H*.75)])
    c2=Polygon([(L*.45,H*.32),(L*.92,H*.32),(L*.92,H*.68),(L*.45,H*.68)])
    ang=_ang(32)
    def cerchio(cx,r): return Polygon([(cx+r*math.cos(a),H*.5+r*math.sin(a)) for a in ang])
    def foro(cx,r):    return Polygon([(cx+r*.40*math.cos(a),H*.5+r*.40*math.sin(a)) for a in ang])
    tl=cerchio(L*.04,H*.48); fl=foro(L*.04,H*.48)
    tr=cerchio(L*.95,H*.35); fr=foro(L*.95,H*.35)
    u=unary_union([c1,c2,tl,tr]).difference(unary_union([fl,fr]))
    b=u.bounds; return translate(u,-b[0],-b[1])

def _sh_staffa(L,H):
    z=Polygon([(0,0),(L,0),(L,H*.35),(L*.30,H*.35),(L*.30,H*.65),(L,H*.65),(L,H),(0,H),
               (0,H*.65),(L*.70,H*.65),(L*.70,H*.35),(0,H*.35)])
    ang=_ang(20); r=min(L,H)*.10
    fori=[Polygon([(fx+r*math.cos(a),fy+r*math.sin(a)) for a in ang])
          for fx,fy in [(L*.18,H*.17),(L*.82,H*.17),(L*.18,H*.83),(L*.82,H*.83)]]
    u=z.difference(unary_union(fori)); b=u.bounds; return translate(u,-b[0],-b[1])

def _sh_coperchio(L,H):
    cut=min(L,H)*.12; cx,cy=L/2,H/2
    oct=Polygon([(cut,0),(L-cut,0),(L,cut),(L,H-cut),(L-cut,H),(cut,H),(0,H-cut),(0,cut)])
    ang=_ang(24); rf=min(L,H)*.22; rb=min(L,H)*.07
    foro=Polygon([(cx+rf*math.cos(a),cy+rf*math.sin(a)) for a in ang])
    fb=[Polygon([(fx+rb*math.cos(a),fy+rb*math.sin(a)) for a in ang])
        for fx,fy in [(L*.20,H*.20),(L*.80,H*.20),(L*.20,H*.80),(L*.80,H*.80)]]
    u=oct.difference(unary_union([foro]+fb)); b=u.bounds; return translate(u,-b[0],-b[1])

def _sh_piastra(L,H):
    p=Polygon([(0,0),(L,0),(L,H),(0,H)])
    ang=_ang(20); r=min(L,H)*.10; cx,cy=L/2,H/2
    fo=Polygon([(cx+L*.18*math.cos(a),cy+H*.22*math.sin(a)) for a in ang])
    fb=[Polygon([(fx+r*math.cos(a),fy+r*math.sin(a)) for a in ang])
        for fx,fy in [(L*.18,H*.20),(L*.82,H*.20),(L*.18,H*.80),(L*.82,H*.80)]]
    u=p.difference(unary_union([fo]+fb)); b=u.bounds; return translate(u,-b[0],-b[1])

DISPATCHER={
    "Trinciatrici":_sh_trincia,"Rotopresse":_sh_rotopresse,
    "Vendemmiatrici":_sh_vendemmiatrice,"ENODUO":_sh_enoduo,
    "Falciatrici":_sh_falciatrice,"Traino":_sh_barra_traino,
    "Carter":_sh_carter,"Carrello":_sh_ruota,"Bracci":_sh_braccio,
    "Staffaggi":_sh_staffa,"Coperchi":_sh_coperchio,
}

def calcola_silhouette(p:PezzoNesting)->PezzoNesting:
    try:
        fn=DISPATCHER.get(p.famiglia,_sh_piastra)
        shape=fn(p.L_mm,p.H_mm)
        if shape is None or shape.is_empty or not shape.is_valid:
            raise ValueError
    except Exception:
        shape=Polygon([(0,0),(p.L_mm,0),(p.L_mm,p.H_mm),(0,p.H_mm)])
    bd=shape.bounds; p.shape=shape; p.sw_mm=bd[2]-bd[0]; p.sh_mm=bd[3]-bd[1]
    return p

@dataclass
class BloccoGancio:
    pezzo:PezzoNesting; slot:int; y0_mm:float; principale:bool=True

@dataclass
class Gancio:
    idx:int; blocchi:List[BloccoGancio]=field(default_factory=list)
    peso_tot:float=0.; y_top:float=0.

def alloca_pezzi(pezzi,n_ganci,h_max_mm=2000.,peso_max_kg=60.,gap_mm=30.):
    for i,p in enumerate(pezzi):
        if not p.colore: p.colore=COLORI_FAMIGLIA.get(p.famiglia,"#455A64")
        if p.shape is None: calcola_silhouette(p)
    ganci=[Gancio(i) for i in range(n_ganci)]
    queue=[]
    for p in pezzi:
        for _ in range(p.qty): queue.append(p)
    queue.sort(key=lambda x:(-x.ganci_req,-x.peso_kg))
    for p in queue:
        ng=p.ganci_req; ph=p.sh_mm; placed=False
        if ng>1:
            for i in range(n_ganci-ng+1):
                blk=ganci[i:i+ng]; y0=max(g.y_top for g in blk); pw=p.peso_kg/ng
                if y0+ph<=h_max_mm and all(g.peso_tot+pw<=peso_max_kg for g in blk):
                    for si,g in enumerate(blk):
                        g.blocchi.append(BloccoGancio(p,si,y0,principale=(si==0)))
                        g.peso_tot+=pw; g.y_top=y0+ph+gap_mm
                    placed=True; break
        if not placed:
            cands=[g for g in ganci if g.peso_tot+p.peso_kg<=peso_max_kg and g.y_top+ph<=h_max_mm]
            if cands:
                best=max(cands,key=lambda g:g.peso_tot)
                best.blocchi.append(BloccoGancio(p,0,best.y_top,principale=True))
                best.peso_tot+=p.peso_kg; best.y_top+=ph+gap_mm
    return ganci

def render_nesting_png(ganci,pezzi,out_path,titolo="PIANO VERNICIATURA",
                       commessa="",n_ganci=10,passo_mm=400.,h_max_mm=2000.,peso_max=60.):
    import matplotlib; matplotlib.use("Agg")
    import matplotlib.pyplot as plt, matplotlib.patches as mpatches
    from matplotlib.patches import Polygon as MPoly, FancyBboxPatch
    import matplotlib.patheffects as pe

    sc=1/100.; CW=passo_mm*sc; AH=h_max_mm*sc; GAP=0.22
    ML=1.0;MT=1.9;MB=1.6;LW=8.5
    N=len(ganci); FW=ML+N*(CW+GAP)+LW+.5; FH=MT+AH+MB

    fig,ax=plt.subplots(figsize=(FW*2.5,FH*2.5),dpi=150)
    ax.set_facecolor("#080B10"); fig.patch.set_facecolor("#080B10")
    ax.set_aspect("equal"); ax.axis("off")
    ax.set_xlim(-ML,N*(CW+GAP)+LW+.4); ax.set_ylim(-MB,AH+MT)

    ax.text(N*(CW+GAP)/2,AH+1.35,titolo,color="#F0F6FC",fontsize=13,fontweight="bold",
            ha="center",va="bottom",family="monospace")
    ax.text(N*(CW+GAP)/2,AH+.95,
            f"{commessa}   |   {N} ganci x {passo_mm:.0f}mm   |   H utile {h_max_mm:.0f}mm",
            color="#6E7681",fontsize=7.5,ha="center",va="bottom")

    ry=AH+.40
    ax.plot([-0.1,N*(CW+GAP)-GAP+.1],[ry,ry],color="#58A6FF",lw=7,solid_capstyle="round",zorder=14)
    ax.plot([-0.1,N*(CW+GAP)-GAP+.1],[ry+.07,ry+.07],color="#1F4E8C",lw=2.5,solid_capstyle="round",zorder=14)
    ax.text(-.15,ry,"<< INGRESSO",color="#58A6FF",fontsize=7.5,va="center",ha="right")
    ax.text(N*(CW+GAP)-GAP+.15,ry,"USCITA >>",color="#58A6FF",fontsize=7.5,va="center",ha="left")

    for gi,g in enumerate(ganci):
        x0=gi*(CW+GAP); gx=x0+CW/2
        # Gancio fisico
        ax.plot([gx,gx],[AH,AH+.32],color="#9CA3AF",lw=4,solid_capstyle="round",zorder=15)
        ax.plot([gx-.22,gx+.22],[AH+.32,AH+.32],color="#9CA3AF",lw=6,solid_capstyle="round",zorder=15)
        theta=np.linspace(np.pi,0,30); hr=.09; hcx=gx+.22-hr; hcy=AH+.32-hr
        ax.plot(hcx+hr*np.cos(theta),hcy+hr*np.sin(theta),color="#9CA3AF",lw=6,solid_capstyle="round",zorder=15)
        ax.plot([hcx-hr,hcx-hr+.05],[hcy,hcy-.05],color="#9CA3AF",lw=6,solid_capstyle="round",zorder=15)
        ax.text(gx,AH+.78,f"G{gi+1}",color="#E6EDF3",fontsize=9,ha="center",va="bottom",fontweight="bold")
        ax.text(gx,AH+.55,f"{gi*passo_mm/1000:.2f}m",color="#6E7681",fontsize=6.5,ha="center",va="bottom")

        over=g.peso_tot>peso_max
        ax.add_patch(FancyBboxPatch((x0,0),CW,AH,boxstyle="round,pad=0.04",lw=1.2,
                     edgecolor="#F85149" if over else "#1C2128",facecolor="#0D1117",zorder=1))
        for hh in [500,1000,1500]:
            yg=hh*sc
            ax.plot([x0+.04,x0+CW-.04],[yg,yg],"--",color="#161B22",lw=0.7,zorder=2)
            ax.text(x0+.05,yg+.025,f"{hh}",color="#21262D",fontsize=4.5,va="bottom")

        for b in g.blocchi:
            if not b.principale: continue
            p=b.pezzo; ng=p.ganci_req; span_w=ng*CW+(ng-1)*GAP
            sh=p.shape; bds=sh.bounds; sw=bds[2]-bds[0]; sh_h=bds[3]-bds[1]
            scx=span_w/(sw*sc)*.88; y0p=b.y0_mm*sc; col=p.colore
            polys=list(sh.geoms) if hasattr(sh,"geoms") else [sh]
            for poly in polys:
                if poly.is_empty or not poly.is_valid: continue
                raw=np.array(poly.exterior.coords)
                xs=x0+(raw[:,0]-bds[0])*sc*scx+span_w*.06
                ys=y0p+(raw[:,1]-bds[1])*sc
                ax.add_patch(MPoly(np.column_stack([xs,ys]),closed=True,
                                   facecolor=col+"E0",edgecolor="white",lw=1.2,zorder=3))
                ax.add_patch(MPoly(np.column_stack([xs,ys]),closed=True,
                                   facecolor="none",edgecolor=col,lw=3.,alpha=.30,zorder=2))
                for hole in poly.interiors:
                    hr2=np.array(hole.coords)
                    hxs=x0+(hr2[:,0]-bds[0])*sc*scx+span_w*.06
                    hys=y0p+(hr2[:,1]-bds[1])*sc
                    ax.add_patch(MPoly(np.column_stack([hxs,hys]),closed=True,
                                       facecolor="#0D1117",edgecolor="#FFFFFF55",lw=.6,zorder=4))
            cx=x0+span_w/2; cy=y0p+sh_h*sc/2
            fx=[pe.withStroke(linewidth=3,foreground="black")]
            ax.text(cx,cy+sh_h*sc*.14,p.cod,color="white",fontsize=7.5,
                    ha="center",va="center",fontweight="bold",zorder=6,path_effects=fx)
            ax.text(cx,cy-sh_h*sc*.08,f"{int(sw)}x{int(sh_h)}mm",
                    color="#D1D5DB",fontsize=5.5,ha="center",va="center",zorder=6,path_effects=fx)
            ax.text(cx,cy-sh_h*sc*.28,f"{p.peso_kg:.1f}kg",
                    color="#FFA657",fontsize=5.5,ha="center",va="center",fontweight="bold",zorder=6,path_effects=fx)

        pct=min(g.peso_tot/peso_max,1.); bc="#F85149" if pct>1 else("#F0883E" if pct>.8 else "#3FB950")
        ax.add_patch(mpatches.Rectangle((x0,-.72),CW,.14,facecolor="#161B22",zorder=3))
        ax.add_patch(mpatches.Rectangle((x0,-.72),CW*pct,.14,facecolor=bc,zorder=4))
        ax.text(gx,-.80,f"{g.peso_tot:.0f}kg",color=bc,fontsize=7,ha="center",va="top",fontweight="bold")
        ax.text(gx,-1.05,f"{pct*100:.0f}%",color="#6E7681",fontsize=6,ha="center",va="top")

    lx=N*(CW+GAP)+.45
    ax.text(lx,AH+.05,"COMPONENTI",color="#E6EDF3",fontsize=9,fontweight="bold",va="top")
    ax.plot([lx,lx+LW-.5],[AH-.14,AH-.14],color="#21262D",lw=.7)
    pezzi_unici=list({p.cod:p for p in pezzi}.values())
    for yi,p in enumerate(pezzi_unici):
        ly=AH-.50-yi*.78
        if p.shape and not p.shape.is_empty:
            bd2=p.shape.bounds; sw2=bd2[2]-bd2[0]; sh2=bd2[3]-bd2[1]
            scm=min(.38/max(sw2*sc,.01),.52/max(sh2*sc,.01))
            polys2=list(p.shape.geoms) if hasattr(p.shape,"geoms") else [p.shape]
            for poly2 in polys2:
                if poly2.is_empty: continue
                raw2=np.array(poly2.exterior.coords)
                mxs=lx+.02+(raw2[:,0]-bd2[0])*sc*scm
                mys=ly+.02+(raw2[:,1]-bd2[1])*sc*scm
                ax.add_patch(MPoly(np.column_stack([mxs,mys]),closed=True,
                                   facecolor=p.colore+"CC",edgecolor="white",lw=.6,zorder=5))
                for hole in poly2.interiors:
                    hr3=np.array(hole.coords)
                    hxm=lx+.02+(hr3[:,0]-bd2[0])*sc*scm
                    hym=ly+.02+(hr3[:,1]-bd2[1])*sc*scm
                    ax.add_patch(MPoly(np.column_stack([hxm,hym]),closed=True,
                                       facecolor="#080B10",edgecolor="white",lw=.4,zorder=6))
        else:
            ax.add_patch(mpatches.Rectangle((lx,ly),.38,.50,facecolor=p.colore+"CC",edgecolor="white",lw=.6,zorder=5))
        ax.text(lx+.52,ly+.42,p.cod,color="white",fontsize=8.5,fontweight="bold",va="center")
        ax.text(lx+.52,ly+.26,p.nome,color="#8B949E",fontsize=6.5,va="center")
        ax.text(lx+.52,ly+.10,f"{p.famiglia}  {p.ganci_req}g  {p.peso_kg}kg  x{p.qty}",
                color="#6E7681",fontsize=5.8,va="center")

    usati=sum(1 for g in ganci if g.peso_tot>0); ptot=sum(g.peso_tot for g in ganci)
    sy=AH-len(pezzi_unici)*.78-1.1
    ax.plot([lx,lx+LW-.5],[sy+.22,sy+.22],color="#21262D",lw=.7)
    for lbl,val in [("Ganci occupati",f"{usati}/{N} ({usati/N*100:.0f}%)"),
                    ("Peso totale",f"{ptot:.1f} kg"),("Saturazione",f"{ptot/N/peso_max*100:.0f}%"),
                    ("Slot catena",f"{N*passo_mm/1000:.1f} m")]:
        ax.text(lx,sy,lbl+":",color="#6E7681",fontsize=7,va="top")
        ax.text(lx+4.2,sy,val,color="#E6EDF3",fontsize=7,va="top",fontweight="bold")
        sy-=.36

    plt.tight_layout(pad=.2)
    plt.savefig(out_path,dpi=180,bbox_inches="tight",facecolor="#080B10",edgecolor="none")
    plt.close()

if __name__=="__main__":
    print("Catalogo ENOROSSI — silhouette 2D:")
    for p in CATALOGO_ENOROSSI:
        calcola_silhouette(p)
        print(f"  {p.cod:15s} {p.famiglia:15s} {p.sw_mm:5.0f}x{p.sh_mm:5.0f}mm  {p.shape.geom_type}")
    ganci=alloca_pezzi(CATALOGO_ENOROSSI,n_ganci=10,h_max_mm=2000,peso_max_kg=60)
    render_nesting_png(ganci,CATALOGO_ENOROSSI,
        "/mnt/user-data/outputs/nesting_enorossi.png",
        titolo="PIANO VERNICIATURA — NESTING CATENA LOOP",
        commessa="Catalogo ENOROSSI — Demo slot 4m",n_ganci=10,passo_mm=400,h_max_mm=2000)
    print("Salvato: nesting_enorossi.png")
