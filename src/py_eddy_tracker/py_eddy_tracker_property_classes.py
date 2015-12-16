# -*- coding: utf-8 -*-

"""
===========================================================================
This file is part of py-eddy-tracker.

    py-eddy-tracker is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    py-eddy-tracker is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with py-eddy-tracker.  If not, see <http://www.gnu.org/licenses/>.

Copyright (c) 2014-2015 by Evan Mason
Email: emason@imedea.uib-csic.es
===========================================================================


py_eddy_tracker_amplitude.py

Version 2.0.3

===========================================================================

"""
from scipy.interpolate import griddata
# from scipy.ndimage import generate_binary_structure, binary_erosion
from scipy.ndimage import binary_erosion
from scipy.ndimage import minimum_filter
import numpy as np
from netCDF4 import Dataset
from .tools import index_from_nearest_path, distance_matrix
from . import VAR_DESCR, VAR_DESCR_inv
import logging


class EddiesObservations(object):
    """
    Class to hold eddy properties *amplitude* and counts of
    *local maxima/minima* within a closed region of a sea level anomaly field.

    Variables:
      centlon:
        Longitude centroid coordinate

      centlat:
        Latitude centroid coordinate

      eddy_radius_s:
        Speed based radius

      eddy_radius_e:
        Effective radius

      amplitude:
        Eddy amplitude

      uavg:
        Average eddy swirl speed

      teke:
        Average eddy kinetic energy within eddy

      rtime:
        Time
    """

    def __init__(self, size=0, track_extra_variables=False):
        self.track_extra_variables = track_extra_variables
        for elt in self.elements:
            if elt not in VAR_DESCR:
                raise Exception('Unknown element : %s' % elt)
        self.observations = np.zeros(size, dtype=self.dtype)
        self.active = True
        self.sign_type = None

    def __repr__(self):
        return str(self.observations)

    @property
    def dtype(self):
        dtype = list()
        for elt in self.elements:
            dtype.append((elt, VAR_DESCR[elt][
                'compute_type' if 'compute_type' in VAR_DESCR[elt] else
                'nc_type']))
        return dtype

    @property
    def elements(self):
        elements = [
            'lon',  # 'centlon'
            'lat',  # 'centlat'
            'radius_s',  # 'eddy_radius_s'
            'radius_e',  # 'eddy_radius_e'
            'amplitude',  # 'amplitude'
            'speed_radius',  # 'uavg'
            'eke',  # 'teke'
            'time']  # 'rtime'

        if self.track_extra_variables:
            elements += ['contour_e',
                         'contour_s',
                         'uavg_profile',
                         'shape_error',
                         ]
        return elements

    def coherence(self, other):
        return self.track_extra_variables == other.track_extra_variables
    
    def merge(self, other):
        nb_obs_self = len(self)
        nb_obs = nb_obs_self + len(other)
        eddies = self.__class__(size=nb_obs)
        eddies.obs[:nb_obs_self] = self.obs[:]
        eddies.obs[nb_obs_self:] = other.obs[:]
        eddies.sign_type = self.sign_type
        return eddies

    def reset(self):
        self.observations = np.zeros(0, dtype=self.dtype)

    @property
    def obs(self):
        return self.observations

    def __len__(self):
        return len(self.observations)

    def __iter__(self):
        for obs in self.obs:
            yield obs

    def insert_observations(self, other, index):
        if not self.coherence(other):
            raise Exception('Observations with no coherence')
        insert_size = len(other.obs)
        self_size = len(self.obs)
        new_size = self_size + insert_size
        if self_size == 0:
            self.observations = other.obs
            return self
        elif insert_size == 0:
            return self
        if index < 0:
            index = self_size + index + 1
        eddies = self.__class__(new_size, self.track_extra_variables)
        eddies.obs[:index] = self.obs[:index]
        eddies.obs[index: index + insert_size] = other.obs
        eddies.obs[index + insert_size:] = self.obs[index:]
        self.observations = eddies.obs
        return self

    def append(self, other):
        return self + other

    def __add__(self, other):
        return self.insert_observations(other, -1)

    def distance_matrix(self, other):
        """ Use haversine distance for distance matrix between every old and
        new eddy"""
        dist_mat = np.empty((len(self), len(other)))
        distance_matrix(self.obs['lon'], self.obs['lat'],
                        other.obs['lon'], other.obs['lat'],
                        dist_mat)
        return dist_mat

    def index(self, index):
        size = 1
        if hasattr(index, '__iter__'):
            size = len(index)
        eddies = self.__class__(size, self.track_extra_variables)
        eddies.obs[:] = self.obs[index]
        return eddies
    
    @staticmethod
    def load_from_netcdf(filename):
        with Dataset(filename) as h_nc:
            nb_obs = len(h_nc.dimensions['Nobs'])
            eddies = EddiesObservations(size=nb_obs)
            for variable in h_nc.variables:
                if variable == 'cyc':
                    continue
                eddies.obs[VAR_DESCR_inv[variable]] = h_nc.variables[variable][:]
            eddies.sign_type = h_nc.variables['cyc'][0]
        return eddies


class VirtualEddiesObservations(EddiesObservations):
    
    @property
    def elements(self):
        elements = super(VirtualEddiesObservations, self).elements
        elements.extend(['track'])
        return elements
    
class TrackEddiesObservations(EddiesObservations):
    
    def extract_longer_eddies(self, nb_min, nb_obs):
        m = nb_obs >= nb_min
        nb_obs_select = m.sum()
        logging.info('Selection of %d observations', nb_obs_select)
        eddies = TrackEddiesObservations(size=nb_obs_select)
        eddies.sign_type = self.sign_type
        for var, _ in eddies.obs.dtype.descr:
            eddies.obs[var] = self.obs[var][m]
        return eddies
    
    @property
    def elements(self):
        elements = super(TrackEddiesObservations, self).elements
        elements.extend(['track', 'n', 'virtual'])
        return elements
    
    def create_variable(self, handler_nc, kwargs_variable,
                        attr_variable, data, scale_factor=None):
        var = handler_nc.createVariable(
            zlib=True,
            complevel=1,
            **kwargs_variable)
        for attr, attr_value in attr_variable.iteritems():
            var.setncattr(attr, attr_value)
            
        var[:] = data
        
        #~ var.set_auto_maskandscale(False)
        if scale_factor is not None:
            var.scale_factor = scale_factor
            
        try:
            var.setncattr('min', var[:].min())
            var.setncattr('max', var[:].max())
        except ValueError:
            logging.warn('Data is empty')

    def write_netcdf(self):
        """Write a netcdf with eddy obs
        """
        eddy_size = len(self.observations)
        sign_type = 'Cyclonic' if self.sign_type == -1 else 'Anticyclonic'
        filename = '%s.nc' % sign_type
        with Dataset(filename, 'w', format='NETCDF4') as h_nc:
            logging.info('Create file %s', filename)
            # Create dimensions
            logging.debug('Create Dimensions "Nobs" : %d', eddy_size)
            h_nc.createDimension('Nobs', eddy_size)
            # Iter on variables to create:
            for name, _ in self.observations.dtype.descr:
                logging.debug('Create Variable %s', VAR_DESCR[name]['nc_name'])
                self.create_variable(
                    h_nc,
                    dict(varname=VAR_DESCR[name]['nc_name'],
                         datatype=VAR_DESCR[name]['nc_type'],
                         dimensions=VAR_DESCR[name]['nc_dims']),
                    VAR_DESCR[name]['nc_attr'],
                    self.observations[name],
                    scale_factor=None if 'scale_factor' not in VAR_DESCR[name] else VAR_DESCR[name]['scale_factor'])

            # Add cyclonic information
            self.create_variable(
                h_nc,
                dict(varname=VAR_DESCR['type_cyc']['nc_name'],
                     datatype=VAR_DESCR['type_cyc']['nc_type'],
                     dimensions=VAR_DESCR['type_cyc']['nc_dims']),
                VAR_DESCR['type_cyc']['nc_attr'],
                self.sign_type)
            # Global attr
            self.set_global_attr_netcdf(h_nc)

    def set_global_attr_netcdf(self, h_nc):
        h_nc.title = 'Cyclonic' if self.sign_type == -1 else 'Anticyclonic' + ' eddy tracks'
        #~ h_nc.grid_filename = self.grd.grid_filename
        #~ h_nc.grid_date = str(self.grd.grid_date)
        #~ h_nc.product = self.product

        #~ h_nc.contour_parameter = self.contour_parameter
        #~ h_nc.shape_error = self.shape_error
        #~ h_nc.pixel_threshold = self.pixel_threshold

        #~ if self.smoothing in locals():
            #~ h_nc.smoothing = self.smoothing
            #~ h_nc.SMOOTH_FAC = self.SMOOTH_FAC
        #~ else:
            #~ h_nc.smoothing = 'None'

        #~ h_nc.evolve_amp_min = self.evolve_amp_min
        #~ h_nc.evolve_amp_max = self.evolve_amp_max
        #~ h_nc.evolve_area_min = self.evolve_area_min
        #~ h_nc.evolve_area_max = self.evolve_area_max
#~ 
        #~ h_nc.llcrnrlon = self.grd.lonmin
        #~ h_nc.urcrnrlon = self.grd.lonmax
        #~ h_nc.llcrnrlat = self.grd.latmin
        #~ h_nc.urcrnrlat = self.grd.latmax


class Amplitude (object):
    """
    Class to calculate *amplitude* and counts of *local maxima/minima*
    within a closed region of a sea level anomaly field.

    Attributes:
      contlon:
        Longitude coordinates of contour

      contlat:
        Latitude coordinates of contour

      eddy:
        A tracklist object holding the SLA data

      grd:
        A grid object
    """
    def __init__(self, contlon, contlat, eddy, grd):
        """
        """
        self.contlon = contlon.copy()
        self.contlat = contlat.copy()
        eddy.grd = grd  # temporary fix
        self.eddy = eddy
        self.sla = self.eddy.sla[self.jslice,
                                 self.islice].copy()

        if 'RectBivariate' in eddy.interp_method:
            h_0 = grd.sla_coeffs.ev(self.contlat[1:], self.contlon[1:])

        elif 'griddata' in eddy.interp_method:
            points = np.array([grd.lon()[self.jslice, self.islice].ravel(),
                               grd.lat()[self.jslice, self.islice].ravel()]).T
            h_0 = griddata(points, self.sla.ravel(),
                           (self.contlon[1:], self.contlat[1:]),
                           'linear')
        else:
            raise Exception('Unknown method : %s' % eddy.interp_method)

        self.h_0 = h_0[np.isfinite(h_0)].mean()
        self.amplitude = 0  # np.atleast_1d(0.)
        self.local_extrema = None  # np.int(0)
        self.local_extrema_inds = None
        self.sla = np.ma.masked_where(-self.mask, self.sla)

    @property
    def islice(self):
        return self.eddy.slice_i

    @property
    def jslice(self):
        return self.eddy.slice_j

    @property
    def mask(self):
        return self.eddy.mask_eff

    @property
    def mle(self):
        return self.eddy.max_local_extrema
    
    def within_amplitude_limits(self):
        """
        """
        return (self.amplitude >= self.eddy.ampmin and
                self.amplitude <= self.eddy.ampmax)

    def _set_cyc_amplitude(self):
        """
        """
        self.amplitude = self.h_0
        self.amplitude -= self.sla.min()

    def _set_acyc_amplitude(self):
        """
        """
        self.amplitude = self.sla.max()
        self.amplitude -= self.h_0

    def all_pixels_below_h0(self, level):
        """
        Check CSS11 criterion 1: The SSH values of all of the pixels
        are below a given SSH threshold for cyclonic eddies.
        """
        if np.any(self.sla > self.h_0):
            return False  # i.e., with self.amplitude == 0
        else:
            self._set_local_extrema(1)
            if (self.local_extrema > 0 and
                    self.local_extrema <= self.mle):
                self._set_cyc_amplitude()
            elif self.local_extrema > self.mle:
                lmi_j, lmi_i = np.where(self.local_extrema_inds)
                levnm2 = level - (2 * self.eddy.interval)
                slamin = 1e5
                for j, i in zip(lmi_j, lmi_i):
                    if slamin >= self.sla[j, i]:
                        slamin = self.sla[j, i]
                        jmin, imin = j, i
                    if self.sla[j, i] >= levnm2:
                        self._set_cyc_amplitude()
                        # Prevent further calls to_set_cyc_amplitude
                        levnm2 = 1e5
                jmin += self.eddy.jmin
                imin += self.eddy.imin
                return (imin, jmin)
        return False

    def all_pixels_above_h0(self, level):
        """
        Check CSS11 criterion 1: The SSH values of all of the pixels
        are above a given SSH threshold for anticyclonic eddies.
        """
        if np.any(self.sla < self.h_0):
            # i.e.,with self.amplitude == 0
            return False
        else:
            self._set_local_extrema(-1)
            if (self.local_extrema > 0 and
                    self.local_extrema <= self.mle):
                self._set_acyc_amplitude()

            elif self.local_extrema > self.mle:
                lmi_j, lmi_i = np.where(self.local_extrema_inds)
                levnp2 = level + (2 * self.eddy.interval)
                slamax = -1e5
                for j, i in zip(lmi_j, lmi_i):
                    if slamax <= self.sla[j, i]:
                        slamax = self.sla[j, i]
                        jmax, imax = j, i
                    if self.sla[j, i] <= levnp2:
                        self._set_acyc_amplitude()
                        # Prevent further calls to_set_acyc_amplitude
                        levnp2 = -1e5
                jmax += self.eddy.jmin
                imax += self.eddy.imin
                return (imax, jmax)
        return False

    def _set_local_extrema(self, sign):
        """
        Set count of local SLA maxima/minima within eddy
        """
        self._detect_local_minima(self.sla * sign)

    def _detect_local_minima(self, arr):
        """
        Take an array and detect the troughs using the local maximum filter.
        Returns a boolean mask of the troughs (i.e., 1 when
        the pixel's value is the neighborhood maximum, 0 otherwise)
        http://stackoverflow.com/questions/3684484/peak-detection-in-a-2d-array/3689710#3689710
        """
        # Equivalent
        neighborhood = np.ones((3, 3), dtype='bool')
        #~ neighborhood = generate_binary_structure(arr.ndim, 2)

        # Get local mimima
        detected_minima = minimum_filter(
            arr, footprint=neighborhood) == arr
        background = (arr == 0)
        # Aims ?
        eroded_background = binary_erosion(
            background, structure=neighborhood, border_value=1)
        detected_minima -= eroded_background
        # mask of minima
        self.local_extrema_inds = detected_minima
        # nb of minima
        self.local_extrema = detected_minima.sum()


class SwirlSpeed(object):
    """
    Class to calculate average geostrophic velocity along
    a contour, *uavg*, and return index to contour with maximum
    *uavg* within a series of closed contours.

    Attributes:
      contour:
        A matplotlib contour object of high-pass filtered SLA

      eddy:
        A tracklist object holding the SLA data

      grd:
        A grid object
    """
    def __init__(self, contour):
        """
        c_i : index to contours
        l_i : index to levels
        """
        x_list, y_list, ci_list, li_list = [], [], [], []

        for cont in contour.collections:
            for coll in cont.get_paths():
                x_list.append(coll.vertices[:, 0])
                y_list.append(coll.vertices[:, 1])
                ci_list.append(len(coll.vertices[:, 0]))
            li_list.append(len(cont.get_paths()))

        self.x_value = np.array([val for sublist in x_list for val in sublist])
        self.y_value = np.array([val for sublist in y_list for val in sublist])
        self.nb_pt_per_c = np.array(ci_list, dtype='u4')
        self.c_i = np.array(self.nb_pt_per_c.cumsum() - self.nb_pt_per_c,
                            dtype='u4')
        self.nb_c_per_l = np.array(li_list, dtype='u4')
        self.l_i = np.array(self.nb_c_per_l.cumsum() - self.nb_c_per_l,
                            dtype='u4')

    def get_index_nearest_path(self, level, xpt, ypt):
        """
        """
        return index_from_nearest_path(
            level,
            self.l_i,
            self.nb_c_per_l,
            self.nb_pt_per_c,
            self.c_i,
            self.x_value,
            self.y_value,
            xpt,
            ypt
            )
