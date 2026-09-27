"""Bounded DICOM LPS geometry. Arrays are [z,y,x], positions are [x,y,z] mm."""
from dataclasses import dataclass
from io import BytesIO
import numpy as np
import pydicom
import warnings
from functools import wraps
from matplotlib.path import Path as Polygon
from scipy.interpolate import RegularGridInterpolator


class InputError(ValueError):
    """Safe error text suitable for display; never include DICOM identifiers."""


def safe_dicom_diagnostics(function):
    """Untrusted DICOM values must not be echoed by pydicom validation warnings."""
    @wraps(function)
    def wrapped(*args,**kwargs):
        with warnings.catch_warnings():
            warnings.filterwarnings('ignore',module=r'pydicom(\..*)?')
            return function(*args,**kwargs)
    return wrapped


@safe_dicom_diagnostics
def read_dicom(source, modality):
    try:
        ds = pydicom.dcmread(BytesIO(source) if isinstance(source, bytes) else source)
    except Exception as exc:
        raise InputError('DICOM konnte nicht gelesen werden.') from exc
    if ds.get('Modality') != modality:
        raise InputError(f'Erwartet wird {modality}.')
    return ds


@safe_dicom_diagnostics
def structure_rois(ds):
    names = {int(r.ROINumber): str(r.ROIName) for r in ds.StructureSetROISequence}
    frames = {int(r.ROINumber): str(r.get('ReferencedFrameOfReferenceUID','')) for r in ds.StructureSetROISequence}
    rois = {}
    for r in ds.get('ROIContourSequence', []):
        number = int(r.ReferencedROINumber)
        native = source_planes(r)
        contours = []
        types = set()
        unsupported = False
        for c in r.get('ContourSequence', []):
            kind = str(c.ContourGeometricType)
            if kind not in ('CLOSED_PLANAR', 'CLOSEDPLANAR_XOR'):
                unsupported = True
                continue
            a = np.asarray(c.ContourData, dtype=float).reshape(-1, 3)
            if len(a) < 3 or not np.isfinite(a).all():
                raise InputError('Ungültige Konturpunkte.')
            if native is not None:
                q = (a-native['origin']) @ native['basis'].T
                k = int(np.rint(np.mean(q[:,2])/native['spacing']))
                if k < 0 or k >= native['frames'] or np.max(np.abs(q[:,2]-k*native['spacing'])) > 0.02:
                    raise InputError('Kontur stimmt nicht mit den angegebenen HDSS-Quellebenen überein.')
            elif np.ptp(a[:, 2]) > 0.02:
                raise InputError('Prototyp unterstützt nur axiale Konturebenen im LPS-System.')
            contours.append(a)
            types.add(kind)
        if contours:
            if len(types) > 1:
                raise InputError('Gemischte XOR- und Standardkonturen werden nicht unterstützt.')
            if unsupported:
                raise InputError('Gemischte geschlossene/offene Konturen: Struktur kann nicht vollständig ausgewertet werden.')
            rois[number] = dict(number=number, name=names.get(number, f'ROI {number}'), contours=contours,frame=frames.get(number,''),source_planes=native)
    if not rois:
        raise InputError('Keine geschlossenen Konturen gefunden.')
    return rois


def source_planes(roi_contour):
    """DICOM C.8.8.6.4: explicit native planes; absent contours mean empty planes."""
    seq=roi_contour.get('SourcePixelPlanesCharacteristicsSequence')
    if seq is None:return None
    try:
        if len(seq)!=1:raise ValueError()
        s=seq[0];ori=np.asarray(s.ImageOrientationPatient,float)
        origin=np.asarray(s.ImagePositionPatient,float)
        spacing=float(s.SpacingBetweenSlices);frames=int(s.NumberOfFrames)
        pixels=np.asarray(s.PixelSpacing,float);rows=int(s.Rows);columns=int(s.Columns)
        if (ori.shape!=(6,) or origin.shape!=(3,) or pixels.shape!=(2,) or
            not np.isfinite(np.r_[ori,origin,pixels,spacing]).all() or
            spacing<=0 or frames<1 or (pixels<=0).any() or min(rows,columns)<1):raise ValueError()
        u,v=ori[:3],ori[3:]
        if not np.allclose([u@u,v@v,u@v],[1,1,0],atol=1e-5):raise ValueError()
        basis=np.array([u,v,np.cross(u,v)])
        return dict(origin=origin,basis=basis,spacing=spacing,frames=frames)
    except (AttributeError,ValueError,TypeError,IndexError):
        raise InputError('Ungültige HDSS-Quellgeometrie.') from None


def structure_frame(ds, numbers):
    frames = {str(r.ReferencedFrameOfReferenceUID) for r in ds.StructureSetROISequence
              if int(r.ROINumber) in numbers}
    if len(frames) != 1 or not all(frames):
        raise InputError('Gewählte Strukturen müssen denselben Referenzrahmen besitzen.')
    return frames.pop()


@dataclass
class Grid:
    origin: np.ndarray
    shape: tuple
    spacing: float = 1.0

    @property
    def axes(self):
        return [self.origin[i] + np.arange(self.shape[2-i])*self.spacing for i in range(3)]

    @property
    def voxel_cc(self):
        return self.spacing**3 / 1000

    def points(self, start=0, stop=None):
        idx = np.arange(start, np.prod(self.shape) if stop is None else stop)
        z, y, x = np.unravel_index(idx, self.shape)
        return np.column_stack((x, y, z))*self.spacing + self.origin


def roi_bounds(roi):
    """Physical contour-slab support, including endpoint half-slice thickness."""
    points=np.concatenate(roi['contours'])
    low,high=points.min(0),points.max(0)
    native=roi.get('source_planes')
    if native is not None:
        half=np.abs(native['basis'][2])*native['spacing']/2
        return low-half,high+half
    # Match rasterize's plane representative exactly, including tolerated z jitter.
    levels=np.array(sorted({round(float(c[0,2]),3) for c in roi['contours']}))
    if len(levels)<2:
        raise InputError('Einzelne Konturebene hat keine eindeutige Dicke.')
    low[2]=levels[0]-(levels[1]-levels[0])/2
    high[2]=levels[-1]+(levels[-1]-levels[-2])/2
    return low,high


def make_grid(rois, targets, spacing=1., padding=35., max_voxels=12_000_000):
    if not targets or len(targets) > 40 or any(n not in rois for n in targets):
        raise InputError('1 bis 40 gültige Zielstrukturen auswählen.')
    # Explicit source planes define physical slab support, even for one plane.
    # Preserve the established grid for legacy contours without source geometry.
    bounds = [roi_bounds(rois[n]) if rois[n].get('source_planes') is not None else
              (np.concatenate(rois[n]['contours']).min(0), np.concatenate(rois[n]['contours']).max(0))
              for n in targets]
    low = np.floor((np.min([b[0] for b in bounds], axis=0)-padding)/spacing)*spacing
    high = np.ceil((np.max([b[1] for b in bounds], axis=0)+padding)/spacing)*spacing
    size = np.rint((high-low)/spacing).astype(int)+1
    if np.prod(size, dtype=np.int64) > max_voxels or np.any(size < 2):
        raise InputError('Geometrie überschreitet den Speicherbereich des SRS-Prototyps.')
    return Grid(low, tuple(int(x) for x in size[::-1]), spacing)


def rasterize(roi, grid):
    """Even-odd planar fill; nearest contour slab bounded by half adjacent gap.

    Includes holes/disjoint loops; contour endpoint slabs extend by half the
    adjacent plane spacing. A single plane is unsupported (unknown thickness).
    No inferred CT geometry, margin expansion or smoothing.
    """
    if roi.get('source_planes') is not None:return rasterize_source_planes(roi,grid)
    planes = {}
    for c in roi['contours']:
        planes.setdefault(round(float(c[0, 2]), 3), []).append(c)
    levels = np.array(sorted(planes))
    if len(levels) < 2:
        raise InputError('Einzelne Konturebene hat keine eindeutige Dicke; mindestens zwei Ebenen nötig.')
    if np.any(np.diff(levels) > 5):
        raise InputError('Konturabstände über 5 mm werden im SRS-Prototyp nicht interpoliert.')
    edges = np.r_[levels[0]-(levels[1]-levels[0])/2,
                  (levels[1:]+levels[:-1])/2,
                  levels[-1]+(levels[-1]-levels[-2])/2]
    xs, ys, zs = grid.axes
    allp = np.concatenate(roi['contours'])
    xi = np.where((xs >= allp[:,0].min()) & (xs <= allp[:,0].max()))[0]
    yi = np.where((ys >= allp[:,1].min()) & (ys <= allp[:,1].max()))[0]
    result = np.zeros(grid.shape, bool)
    if not len(xi) or not len(yi):
        return result
    xx, yy = np.meshgrid(xs[xi], ys[yi])
    points = np.column_stack((xx.ravel(), yy.ravel()))
    for j, level in enumerate(levels):
        zi = np.where((zs >= edges[j]) & (zs < edges[j+1]))[0]
        if not len(zi):
            continue
        inside = np.zeros(len(points), bool)
        for c in planes[level]:
            # Explicitly append first vertex: Path(closed=True) otherwise drops
            # the last supplied vertex from an unclosed polygon.
            p = c[:, :2]
            if not np.allclose(p[0], p[-1]):
                p = np.vstack((p, p[0]))
            inside ^= Polygon(p, closed=True).contains_points(points)
        result[np.ix_(zi, yi, xi)] = inside.reshape(len(yi), len(xi))
    return result


def rasterize_source_planes(roi,grid):
    """Sample native contour slabs in LPS, without flattening or bridging gaps."""
    native=roi['source_planes'];basis=native['basis'];origin=native['origin'];step=native['spacing']
    planes={}
    for c in roi['contours']:
        q=(c-origin)@basis.T;k=int(np.rint(q[:,2].mean()/step));p=q[:,:2]
        if not np.allclose(p[0],p[-1]):p=np.vstack((p,p[0]))
        planes.setdefault(k,[]).append(Polygon(p,closed=True))
    result=np.zeros(grid.shape,bool);low,high=roi_bounds(roi);axes=grid.axes
    indices=[np.flatnonzero((a>=lo-1e-7)&(a<=hi+1e-7)) for a,lo,hi in zip(axes,low,high)]
    xi,yi,zi=indices
    if any(not len(i) for i in indices):return result
    xx,yy=np.meshgrid(axes[0][xi],axes[1][yi]);xyz=np.column_stack((xx.ravel(),yy.ravel(),np.zeros(xx.size)))
    for z in zi:
        xyz[:,2]=axes[2][z];q=(xyz-origin)@basis.T
        # Half-open slabs: an exact shared boundary belongs to the upper plane.
        plane_indices=np.floor(q[:,2]/step+.5+1e-9).astype(np.int64)
        inside=np.zeros(len(q),bool)
        for k in np.unique(plane_indices):
            paths=planes.get(k)
            if not paths:continue
            chosen=np.flatnonzero(plane_indices==k);local=np.zeros(len(chosen),bool)
            for path in paths:local ^= path.contains_points(q[chosen,:2])
            inside[chosen]=local
        result[z][np.ix_(yi,xi)]=inside.reshape(len(yi),len(xi))
    return result


@safe_dicom_diagnostics
def dose_geometry(ds, frame, high_precision=False):
    if str(ds.get('DoseUnits', '')) != 'GY' or str(ds.get('DoseSummationType', '')) != 'PLAN':
        raise InputError('Nur vollständige PLAN-Dosis in Gy wird unterstützt.')
    if str(ds.get('DoseType', '')) != 'PHYSICAL':
        raise InputError('Nur physikalische Dosis wird unterstützt.')
    if str(ds.get('FrameOfReferenceUID', '')) != frame:
        raise InputError('RTDOSE und RTSTRUCT haben unterschiedliche Referenzrahmen; keine automatische Registrierung.')
    ori = np.asarray(ds.ImageOrientationPatient, float)
    xdir, ydir = ori[:3], ori[3:]
    if not np.allclose([np.linalg.norm(xdir), np.linalg.norm(ydir), xdir@ydir], [1,1,0], atol=1e-5):
        raise InputError('Ungültige Orientierung des Dosisrasters.')
    origin = np.asarray(ds.ImagePositionPatient, float)
    offsets = np.asarray(ds.GridFrameOffsetVector, float)
    if len(offsets) < 2 or not np.isfinite(offsets).all():
        raise InputError('Ungültige Dosis-Schichtebenen.')
    # DICOM option (b): absolute axial z coordinates.
    if abs(offsets[0]) > 1e-5:
        if np.allclose(ori, [1,0,0,0,1,0]) and np.isclose(offsets[0], origin[2]):
            offsets = offsets-origin[2]
        else:
            raise InputError('Nicht unterstützte GridFrameOffsetVector-Konvention.')
    spacing = np.asarray(ds.PixelSpacing, float)
    scaling = float(ds.DoseGridScaling)
    if not np.isfinite(spacing).all() or (spacing <= 0).any() or not np.isfinite(scaling) or scaling <= 0:
        raise InputError('Ungültige Dosisskalierung oder Pixelabstände.')
    shape = (len(offsets), int(ds.Rows), int(ds.Columns))
    if int(ds.get('NumberOfFrames',0)) != len(offsets) or any(n<=0 for n in shape):
        raise InputError('Deklarierte Dosisdimensionen passen nicht zu den Schichtebenen.')
    if int(ds.get('SamplesPerPixel',1)) != 1 or int(ds.get('BitsAllocated',0)) not in (16,32):
        raise InputError('Nicht unterstützte Dosis-Pixelkodierung.')
    if np.prod(shape, dtype=np.int64) > 65_000_000:
        raise InputError('Dosisraster zu groß (maximal 65 Millionen Voxel).')
    try:
        values = ds.pixel_array.astype(np.float64 if high_precision else np.float32)*scaling
    except Exception as exc:
        raise InputError('Dosis-Pixeldaten nicht lesbar.') from exc
    if values.shape != shape or not np.isfinite(values).all() or (values < 0).any():
        raise InputError('Ungültige Dosiswerte.')
    if np.all(np.diff(offsets) < 0):
        offsets, values = offsets[::-1], values[::-1]
    if not np.all(np.diff(offsets) > 0):
        raise InputError('Dosis-Schichtebenen sind nicht streng monoton.')
    interp = RegularGridInterpolator((offsets, np.arange(shape[1])*spacing[0],
                                      np.arange(shape[2])*spacing[1]), values,
                                     bounds_error=False, fill_value=np.nan)
    basis = np.column_stack((np.cross(xdir, ydir), ydir, xdir))
    return interp, origin, basis


def sample_dose(ds, frame, grid):
    interp, origin, basis = dose_geometry(ds, frame)
    out = np.empty(np.prod(grid.shape), np.float32)
    for start in range(0, len(out), 200_000):
        stop = min(start+200_000, len(out))
        out[start:stop] = interp((grid.points(start, stop)-origin)@basis)
    return out.reshape(grid.shape)


def display_polyline(points, tolerance_mm=.01, closed=True):
    """Bounded-error polyline simplification; no spline or geometry expansion."""
    p=np.asarray(points,float)
    if len(p)<4:return p.copy()
    if closed and not np.allclose(p[0],p[-1],atol=1e-7):p=np.vstack((p,p[0]))
    keep={0,len(p)-1};stack=[(0,len(p)-1)]
    while stack:
        a,b=stack.pop()
        if b-a<2:continue
        v=p[b]-p[a];length=float(v@v)
        q=p[a+1:b]
        t=np.clip(((q-p[a])@v)/length,0,1) if length>1e-16 else np.zeros(len(q))
        d=np.linalg.norm(q-(p[a]+t[:,None]*v),axis=1)
        j=int(np.argmax(d))
        if d[j]>tolerance_mm:
            i=a+1+j;keep.add(i);stack.extend([(a,i),(i,b)])
    return p[sorted(keep)]


def display_target_contours(rois, targets, z_values, spacing_mm=.2, max_samples=10_000_000):
    """Axial display paths from contour geometry, independently of the dose grid.

    Axial contour slabs use their actual polygon vertices. Oblique slabs are
    sampled at 0.2 mm in each displayed axial plane, with a bounded work budget.
    These visualization paths never participate in metrics or dose export.
    """
    import contourpy
    prepared=[];estimated=0.
    for n in targets:
        roi=rois[n];low,high=roi_bounds(roi)
        zs=[i for i,z in enumerate(z_values) if low[2]-1e-7<=z<=high[2]+1e-7]
        axial=all(np.ptp(c[:,2])<=1e-7 for c in roi['contours'])
        estimated+=0 if axial else len(zs)*np.prod((high[:2]-low[:2])/spacing_mm+4)
        prepared.append((roi,low,high,zs,axial))
    step=spacing_mm*max(1.,np.sqrt(estimated/max_samples))
    result=[[] for _ in z_values]
    for roi,low,high,zs,axial in prepared:
        native=roi.get('source_planes')
        if axial:
            planes={}
            for c in roi['contours']:
                key=int(np.rint(((c[0]-native['origin'])@native['basis'][2])/native['spacing'])) if native else round(float(c[0,2]),3)
                planes.setdefault(key,[]).append(c)
            levels=np.array(sorted(planes))
            if not native:
                edges=np.r_[levels[0]-(levels[1]-levels[0])/2,(levels[1:]+levels[:-1])/2,levels[-1]+(levels[-1]-levels[-2])/2]
            for i in zs:
                z=float(z_values[i])
                if native:
                    key=int(np.floor(((np.array([0.,0.,z])-native['origin'])@native['basis'][2])/native['spacing']+.5+1e-9))
                else:
                    j=int(np.searchsorted(edges,z,side='right')-1)
                    if not 0<=j<len(levels):continue
                    key=levels[j]
                for c in planes.get(key,[]):
                    q=display_polyline(c);q[:,2]=z
                    result[i].append(q.round(4).tolist())
        else:
            origin_xy=np.floor(low[:2]/step)*step-2*step
            size=np.ceil((high[:2]-origin_xy)/step).astype(int)+3
            for i in zs:
                z=float(z_values[i]);g=Grid(np.r_[origin_xy,z],(1,int(size[1]),int(size[0])),step)
                mask=rasterize(roi,g)[0]
                if not mask.any():continue
                x,y,_=g.axes
                for loop in contourpy.contour_generator(x=x,y=y,z=mask.astype(float)).lines(.5):
                    q=display_polyline(np.column_stack((loop,np.full(len(loop),z))))
                    result[i].append(q.round(4).tolist())
    return result,float(step)


def display_isodoses(dose, grid, indices, domain, levels=(5,10,12,18,20,24)):
    """Full-grid dose isolines; preserve open paths at unsupported boundaries."""
    if dose is None:return None
    import contourpy
    x,y,z=grid.axes
    result=[]
    for i in indices:
        values=np.ma.masked_invalid(np.where(domain[i],dose[i],np.nan))
        generator=contourpy.contour_generator(x=x,y=y,z=values)
        paths={}
        for level in levels:
            loops=[]
            for line in generator.lines(level):
                p=np.column_stack((line,np.full(len(line),z[i])))
                loops.append(display_polyline(p,closed=False).round(4).tolist())
            paths[str(level)]=loops
        result.append(paths)
    return result
