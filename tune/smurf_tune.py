import numpy as np
import os
import time
from pysmurf.base import SmurfBase
from scipy import optimize
import scipy.signal as signal


class SmurfTuneMixin(SmurfBase):
    """
    This contains all the tuning scripts
    """

    def tune_band(self, band, freq=None, resp=None, n_samples=2**18, 
        make_plot=True, save_plot=True, save_data=True, grad_cut=.05,
        freq_min=-2.5E8, freq_max=2.5E8, amp_cut=.25):
        """
        This does the full_band_resp, which takes the raw resonance data.
        It then finds the where the reseonances are. Using the resonance
        locations, it does calculates the eta parameters.

        Args:
        -----
        band (int): The band to tune

        Opt Args:
        ---------
        freq (float array): The frequency information. If both freq and resp
            are not None, it will skip full_band_resp.
        resp (float array): The response information. If both freq and resp
            are not None, it will skip full_band_resp.
        n_samples (int): The number of samples to take in full_band_resp.
            Default is 2^18.
        make_plot (bool): Whether to make plots. This is slow, so if you want
            to tune quickly, set to False. Default True.
        save_plot (bool): Whether to save the plot. If True, it will close the
            plots before they are shown. If False, plots will be brought to the
            screen.
        save_data (bool): If True, saves the data to disk.
        grad_cut (float): The value of the gradient of phase to look for 
            resonances. Default is .05
        amp_cut (float): The distance from the median value to decide whether
            there is a resonance. Default is .25.
        freq_min (float): The minimum frequency relative to the center of
            the band to look for resonances. Units of Hz. Defaults is -2.5E8
        freq_max (float): The maximum frequency relative to the center of
            the band to look for resonances. Units of Hz. Defaults is 2.5E8

        Returns:
        --------
        res (dict): A dictionary with resonance frequency, eta, eta_phase,
            R^2, and amplitude.

        """
        timestamp = self.get_timestamp()

        if make_plot and save_plot:
            import matplotlib.pyplot as plt
            plt.ioff()

        if freq is None or resp is None:
            self.log('Running full band resp')
            freq, resp = self.full_band_resp(band, n_samples=n_samples,
                make_plot=make_plot, save_data=save_data, timestamp=timestamp)

        make_subband_plot = False
        if make_plot:
            make_subband_plot = True
        peaks = self.find_peak(freq, resp, band=band, make_plot=make_plot, 
            save_plot=save_plot, grad_cut=grad_cut, freq_min=freq_min,
            freq_max=freq_max, amp_cut=amp_cut, 
            make_subband_plot=make_subband_plot, timestamp=timestamp)

        resonances = {}
        for i, p in enumerate(peaks):
            eta_scaled, eta_phase_deg, r2, amp = self.eta_fit(freq, resp, p, 
                .2e6, 614.4/128, make_plot=make_plot, save_plot=save_plot,
                res_num=i, band=band, timestamp=timestamp)
            resonances[i] = {
                'freq': p,
                'eta': eta_scaled,
                'eta_phase': eta_phase_deg,
                'r2': r2,
                'amp': amp
            }

        if save_data:
            self.log('Saving resonances to {}'.format(self.output_dir))
            np.save(os.path.join(self.output_dir, 
                '{}_b{}_resonances'.format(timestamp, band)), resonances)

        self.log('Assigning channels')

        f = [resonances[k]['freq']*1.0E-6 for k in resonances.keys()]
        subbands, channels, offsets = self.assign_channels(f, band=band)

        for i, k in enumerate(resonances.keys()):
            resonances[k].update({'subband': subbands[i]})
            resonances[k].update({'channel': channels[i]})
            resonances[k].update({'offset': offsets[i]})

        return resonances


    def full_band_resp(self, band, n_samples=2**18, make_plot=False, 
        save_data=False, timestamp=None):
        """
        Injects high amplitude noise with known waveform. The ADC measures it.
        The cross correlation contains the information about the resonances.

        Args:
        -----
        band (int): The band to sweep.

        Opt Args:
        ---------
        n_samples (int): The number of samples to take. Default 2^18.
        make_plot (bool): Whether the make plots. Default is False.
        save_data (bool): Whether to save the plot.
        timestamp (str): The timestamp as a string.

        Returns:
        --------
        f (float array): The frequency information. Length n_samples/2
        resp (complex array): The response information. Length n_samples/2
        """
        if timestamp is None:
            timestamp = self.get_timestamp

        self.set_trigger_hw_arm(0, write_log=True)  # Default setup sets to 1

        self.set_noise_select(band, 1, wait_done=True, write_log=True)
        try:
            adc = self.read_adc_data(band, n_samples, hw_trigger=True)
        except Exception:
            self.log('ADC read failed. Trying one more time')
            adc = self.read_adc_data(band, n_samples, hw_trigger=True)
        time.sleep(.1)  # Need to wait, otherwise dac call interferes with adc

        dac = self.read_dac_data(band, n_samples, hw_trigger=True)
        time.sleep(.05)

        self.set_noise_select(band, 0, wait_done=True, write_log=True)

        if band == 2:
            dac = np.conj(dac)

        # To do : Implement cross correlation to get shift

        f, p_dac = signal.welch(dac, fs=614.4E6, nperseg=n_samples/2)
        f, p_adc = signal.welch(adc, fs=614.4E6, nperseg=n_samples/2)
        f, p_cross = signal.csd(dac, adc, fs=614.4E6, nperseg=n_samples/2)

        idx = np.argsort(f)
        f = f[idx]
        p_dac = p_dac[idx]
        p_adc = p_adc[idx]
        p_cross = p_cross[idx]

        resp = p_cross / p_dac

        if make_plot:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(3, figsize=(5,8), sharex=True)
            f_plot = f / 1.0E6

            plot_idx = np.where(np.logical_and(f_plot>-250, f_plot<250))

            ax[0].semilogy(f_plot, p_dac)
            ax[0].set_ylabel('DAC')
            ax[1].semilogy(f_plot, p_adc)
            ax[1].set_ylabel('ADC')
            ax[2].semilogy(f_plot, np.abs(p_cross))
            ax[2].set_ylabel('Cross')
            ax[2].set_xlabel('Frequency [MHz]')

            plt.tight_layout()

            fig, ax = plt.subplots(1)
            ax.plot(f_plot[plot_idx], np.log10(np.abs(resp[plot_idx])))
            # ax.plot(f_plot, np.real(resp))
            # ax.plot(f_plot, np.imag(resp))

        if save_data:
            save_name = timestamp + '_{}_full_band_resp.txt'
            np.savetxt(os.path.join(self.output_dir, save_name.format('freq')), 
                f)
            np.savetxt(os.path.join(self.output_dir, save_name.format('real')), 
                np.real(resp))
            np.savetxt(os.path.join(self.output_dir, save_name.format('imag')), 
                np.imag(resp))
            
        return f, resp

    def find_peak(self, freq, resp, grad_cut=.05, freq_min=-2.5E8, band=None,
        freq_max=2.5E8, make_plot=False, amp_cut=.25, save_plot=True, 
        make_subband_plot=False, timestamp=None):
        """find the peaks within a given subband

        Args:
        -----
        freq (vector): should be a single row of the broader freq array
        response (complex vector): complex response for just this subband

        Opt Args:
        ---------


        Returns:
        -------_
        resonances (float array): The frequency of the resonances in the band
        """
        if timestamp is None:
            timestamp = self.get_timestamp()

        angle = np.unwrap(np.angle(resp))
        grad = np.ediff1d(angle, to_end=[np.nan])
        amp = np.abs(resp)

        grad_loc = np.array(grad > grad_cut)

        # med = pd.rolling_median(grad, 100, center=True)
        # Really annoying - pandas has several implementations of rolling_median

        window = 500
        import pandas as pd

        med_amp = pd.Series(amp).rolling(window=window, center=True).median()

        starts, ends = self.find_flag_blocks(self.pad_flags(grad_loc, 
            before_pad=20, after_pad=20, min_gap=10))

        peak = np.array([], dtype=int)
        for s, e in zip(starts, ends):
            if freq[s] > freq_min and freq[e] < freq_max:
                idx = np.ravel(np.where(amp[s:e] == np.min(amp[s:e])))[0]
                idx += s
                if med_amp[idx] - amp[idx] > amp_cut:
                    peak = np.append(peak, idx)

        # Make summary plot
        if make_plot:
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(1)

            plot_freq = freq*1.0E-6

            ax.plot(plot_freq,amp)
            ax.plot(plot_freq, med_amp)
            ax.plot(plot_freq[peak], amp[peak], 'kx')

            for s, e in zip(starts, ends):
                ax.axvspan(plot_freq[s], plot_freq[e], color='k', alpha=.1)

            ax.set_ylabel('Amp')
            ax.set_xlabel('Freq [MHz]')

            if save_plot:
                save_name = '{}_plot_freq.png'.format(timestamp)
                plt.savefig(os.path.join(self.plot_dir, save_name))
                plt.close()

        # Make plot per subband
        if make_subband_plot:
            import matplotlib.pyplot as plt
            subbands, subband_freq = self.get_subband_centers(band, 
                hardcode=True)  # remove hardcode mode
            plot_freq = freq * 1.0E-6
            plot_width = 5.5  # width of plotting in MHz
            width = (subband_freq[1] - subband_freq[0])

            for sb, sbf in zip(subbands, subband_freq):
                self.log('Making plot for subband {}'.format(sb))
                idx = np.logical_and(plot_freq > sbf - plot_width/2.,
                    plot_freq < sbf + plot_width/2.)
                f = plot_freq[idx]
                p = angle[idx]
                x = np.arange(len(p))
                fp = np.polyfit(x, p, 1)
                p = p - x*fp[0] - fp[1]

                g = grad[idx]
                a = amp[idx]
                ma = med_amp[idx]

                fig, ax = plt.subplots(2, sharex=True)
                ax[0].plot(f, p, label='Phase')
                ax[0].plot(f, g, label=r'$\Delta$ phase')
                ax[1].plot(f, a, label='Amp')
                ax[1].plot(f, ma, label='Median Amp')
                for s, e in zip(starts, ends):
                    if (plot_freq[s] in f) or (plot_freq[e] in f):
                        ax[0].axvspan(plot_freq[s], plot_freq[e], color='k', 
                            alpha=.1)
                        ax[1].axvspan(plot_freq[s], plot_freq[e], color='k', 
                            alpha=.1)

                for pp in peak:
                    if plot_freq[pp] > sbf - plot_width/2. and \
                        plot_freq[pp] < sbf + plot_width/2.:
                        ax[1].plot(plot_freq[pp], amp[pp], 'xk')

                ax[0].legend(loc='upper right')
                ax[1].legend(loc='upper right')

                ax[0].axvline(sbf, color='k' ,linestyle=':', alpha=.4)
                ax[1].axvline(sbf, color='k' ,linestyle=':', alpha=.4)
                ax[0].axvline(sbf - width/2., color='k' ,linestyle='--', 
                    alpha=.4)
                ax[0].axvline(sbf + width/2., color='k' ,linestyle='--', 
                    alpha=.4)
                ax[1].axvline(sbf - width/2., color='k' ,linestyle='--', 
                    alpha=.4)
                ax[1].axvline(sbf + width/2., color='k' ,linestyle='--', 
                    alpha=.4)

                ax[1].set_xlim((sbf-plot_width/2., sbf+plot_width/2.))

                ax[0].set_ylabel('[Rad]')
                ax[1].set_xlabel('Freq [MHz]')
                ax[1].set_ylabel('Amp')

                ax[0].set_title('Band {} Subband {}'.format(band,
                    sb, sbf))

                if save_plot:
                    save_name = '{}_sb{}_find_freq.png'.format(timestamp, sb)
                    plt.savefig(os.path.join(self.plot_dir, save_name),
                        bbox_inches='tight')
                    plt.close()

        return freq[peak]

    def find_flag_blocks(self, flag, minimum=None, min_gap=None):
        """                                                                                  
        Find blocks of adjacent points in a boolean array with the same value.               
                                                                                             
        Arguments                                                                            
        ---------                                                                            
        flag : bool, array_like 
            The array in which to find blocks 
        minimum : int (optional)
            The minimum length of block to return. Discards shorter blocks 
        min_gap : int (optional)
            The minimum gap between flag blocks. Fills in gaps smaller.

        Returns
        ------- 
        starts, ends : int arrays
            The start and end indices for each block.
            NOTE: the end index is the last index in the block. Add 1 for 
            slicing, where the upper limit should be after the block 
        """
        if min_gap is not None:
            _flag = self.pad_flags(np.asarray(flag, dtype=bool),
                min_gap=min_gap).astype(np.int8)
        else:
            _flag = np.asarray(flag).astype(int)

        marks = np.diff(_flag)
        start = np.where(marks == 1)[0]+1
        if _flag[0]:
            start = np.concatenate([[0],start])
        end = np.where(marks == -1)[0]
        if _flag[-1]:
            end = np.concatenate([end,[len(_flag)-1]])

        if minimum is not None:
            inds = np.where(end - start + 1 > minimum)[0]
            return start[inds],end[inds]
        else:
            return start,end

    def pad_flags(self, f, before_pad=0, after_pad=0, min_gap=0, min_length=0):
        """
        """
        before, after = self.find_flag_blocks(f)
        after += 1 

        inds = np.where(np.subtract(before[1:],after[:-1]) < min_gap)[0]
        after[inds] = before[inds+1]

        before -= before_pad
        after += after_pad

        padded = np.zeros_like(f)

        for b, a in zip(before, after):
            if (a-after_pad)-(b+before_pad) > min_length:
                padded[np.max([0,b]):a] = True

        return padded

    def plot_find_peak(self, freq, resp, peak_ind, save_plot=True, 
        save_name=None):
        """
        """
        import matplotlib.pyplot as plt

        Idat = np.real(resp)
        Qdat = np.imag(resp)
        phase = np.unwrap(np.arctan2(Qdat, Idat))
        
        fig, ax = plt.subplots(2, sharex=True, figsize=(6,4))
        ax[0].plot(freq, np.abs(resp), label='amp', color='b')
        ax[0].plot(freq, Idat, label='I', color='r', linestyle=':', alpha=.5)
        ax[0].plot(freq, Qdat, label='Q', color='g', linestyle=':', alpha=.5)
        ax[0].legend(loc='lower right')
        ax[1].plot(freq, phase, color='b')
        ax[1].set_ylim((-np.pi, np.pi))

        if len(peak_ind):  # empty array returns False
            ax[0].plot(freq[peak_ind], np.abs(resp[peak_ind]), 'x', color='k')
            ax[1].plot(freq[peak_ind], phase[peak_ind], 'x', color='k')
        else:
            self.log('No peak_ind values.', self.LOG_USER)

        fig.suptitle("Peak Finding")
        ax[1].set_xlabel("Frequency offset from Subband Center (MHz)")
        ax[0].set_ylabel("Response")
        ax[1].set_ylabel("Phase [rad]")

        if save_plot:
            if save_name is None:
                self.log('Using default name for saving: find_peak.png \n' +
                    'Highly recommended that you input a non-default name')
                save_name = 'find_peak.png'
            else:
                self.log('Plotting saved to {}'.format(save_name))
            plt.savefig(os.path.join(self.plot_dir, save_name),
                bbox_inches='tight')
            plt.close()

    def eta_fit(self, freq, resp, peak_freq, delF, subbandHalfWidth, 
        make_plot=False, save_plot=True, band=None, timestamp=None, 
        res_num=None):
        """
        Cyndia's eta finding code
        """
        if timestamp is None:
            timestamp = self.get_timestamp()

        amp = np.abs(resp)
        
        fit = np.polyfit(freq, np.unwrap(np.angle(resp)), 1)
        fitted_line = np.poly1d(fit)  
        phase = np.unwrap(np.angle(resp) - fitted_line(freq))
        
        min_idx = np.ravel(np.where(freq == peak_freq))[0]
        
        try:
            left = np.where(freq < peak_freq - delF)[0][-1]
        except IndexError:
            left = 0
        right = np.where(freq > peak_freq + delF)[0][0]
        
            
        eta = (freq[right] - freq[left]) / (resp[right] - resp[left])
        eta_mag = np.abs(eta)
        eta_angle = np.angle(eta)
        eta_scaled = eta_mag * 1e-6/ subbandHalfWidth # convert to MHz
        eta_phase_deg = eta_angle * 180 / np.pi

        def r2_value(x, y, deg):
            fit = np.polyfit(x,y,deg)
            fitted_line = np.poly1d(fit)
            yhat = fitted_line(x)
            ybar = np.sum(y) / len(y)
            ssreg = np.sum((yhat - ybar)**2)
            sstot = np.sum((y - ybar)**2)
            
            return ssreg / sstot

        r2 = r2_value(freq[left:right], phase[left:right],1)
    
        if make_plot:
            self.log('Making plot for band {} res {:03}'.format(band, res_num))
            self.plot_eta_fit(freq[left:right], resp[left:right], 
                eta=eta, eta_mag=eta_mag, eta_angle=eta_angle, r2=r2,
                save_plot=save_plot, timestamp=timestamp, band=band,
                res_num=res_num)

        return eta_scaled, eta_phase_deg, r2, amp

    def plot_eta_fit(self, freq, resp, eta=None, eta_mag=None, 
        eta_angle=None, r2=None, save_plot=True, timestamp=None, 
        res_num=None, band=None):
        """
        """
        if timestamp is None:
            timestamp = self.get_timestamp()

        import matplotlib.pyplot as plt
        from matplotlib.gridspec import GridSpec

        I = np.real(resp)
        Q = np.imag(resp)
        amp = np.sqrt(I**2 + Q**2)
        phase = np.unwrap(np.arctan2(Q, I))  # radians

        plot_freq = freq*1.0E-6

        center_idx = np.ravel(np.where(amp==np.min(amp)))[0]

        fig = plt.figure(figsize=(8,4.5))
        gs=GridSpec(2,3)
        ax0 = fig.add_subplot(gs[0,0])
        ax1 = fig.add_subplot(gs[1,0], sharex=ax0)
        ax2 = fig.add_subplot(gs[:,1:])
        ax0.plot(plot_freq, I, label='I', linestyle=':', color='k')
        ax0.plot(plot_freq, Q, label='Q', linestyle='--', color='k')
        ax0.scatter(plot_freq, amp, c=np.arange(len(freq)), s=3,
            label='amp')
        ax0.legend(fontsize=10, loc='lower right')
        ax0.set_ylabel('Resp')

        idx = np.arange(-5,5.1,5, dtype=int)+center_idx
        ax0.plot(plot_freq[idx], amp[idx], 'rx')

        ax1.scatter(plot_freq, np.rad2deg(phase), c=np.arange(len(freq)), s=3)
        ax1.plot(plot_freq[idx], np.rad2deg(phase[idx]), 'rx')
        ax1.set_ylabel('Phase [deg]')

        # IQ circle
        ax2.axhline(0, color='k', linestyle=':', alpha=.5)
        ax2.axvline(0, color='k', linestyle=':', alpha=.5)

        ax2.scatter(I, Q, c=np.arange(len(freq)), s=3)
        ax2.set_xlabel('I')
        ax2.set_ylabel('Q')

        lab = ''
        if eta is not None:
            if eta_mag is not None:
                lab = r'$\eta/\eta_{mag}$' + \
                ': {:4.3f}+{:4.3f}'.format(np.real(eta/eta_mag), 
                    np.imag(eta/eta_mag)) + '\n'
            else:
                lab = lab + r'$\eta$' + ': {}'.format(eta) + '\n'
        if eta_mag is not None:
            lab = lab + r'$\eta_{mag}$' + ': {:1.3e}'.format(eta_mag) + '\n'
        if eta_angle is not None:
            lab = lab + r'$\eta_{ang}$' + \
                ': {:3.2f}'.format(np.rad2deg(eta_angle)) + '\n'
        if r2 is not None:
            lab = lab + r'$R^2$' + ' :{:4.3f}'.format(r2)

        ax2.text(.03, .80, lab, transform=ax2.transAxes, fontsize=10)

        if eta is not None:
            if eta_mag is not None:
                eta = eta/eta_mag
            respp = eta*resp
            Ip = np.real(respp)
            Qp = np.imag(respp)
            ax2.scatter(Ip, Qp, c=np.arange(len(freq)), cmap='inferno', s=3)

        plt.tight_layout()

        if save_plot:
            if res_num is not None and band is not None:
                save_name = '{}_eta_b{}_res{:03}.png'.format(timestamp, band, 
                    res_num)
            else:
                save_name = '{}_eta.png'.format(timestamp)
            plt.savefig(os.path.join(self.plot_dir, save_name), 
                bbox_inches='tight')
            plt.close()

    def get_closest_subband(self, f, band):
        """
        Returns the closest subband number for a given input frequency.
        
        """
        # get subband centers:
        subbands, centers = self.get_subband_centers(band, as_offset=True)
        if self.check_freq_scale(f, centers[0]):
            pass
        else:
            raise ValueError('{} and {}'.format(f, centers[0]))
            
        idx = np.argmin([abs(x - f) for x in centers])
        return idx

    def check_freq_scale(self, f1, f2):
        """
        """
        if abs(f1/f2) > 1e3:
            return False
        else:
            return True

    def assign_channels(self, freq, band=None, bandcenter=None, 
        channel_per_subband=4):
        """
        """
        if band is None and bandcenter is None:
            self.log('Must have band or bandcenter', self.LOG_ERROR)
            raise ValueError('Must have band or bandcenter')

        subbands = np.zeros(len(freq), dtype=int)
        channels = -1 * np.ones(len(freq), dtype=int)
        offsets = np.zeros(len(freq))
        
        # Assign all frequencies to a subband
        for idx in range(len(freq)):
            subbands[idx] = self.get_closest_subband(freq[idx], band)
            subband_center = self.get_subband_centers(band, 
                as_offset=True)[1][subbands[idx]]

            offsets[idx] = freq[idx] - subband_center
        
        # Assign unique channel numbers
        for unique_subband in set(subbands):
            chans = self.get_channels_in_subband(band, int(unique_subband))
            mask = np.where(subbands == unique_subband)[0]
            if len(mask) > channel_per_subband:
                concat_mask = mask[:channel_per_subband]
            else:
                concat_mask = mask[:]
            
            chans = chans[:len(list(concat_mask)[0])] #I am so sorry
            
            channels[mask[:len(chans)]] = chans
        
        return subbands, channels, offsets

    def setup_notches(self, band, resonance=None, drive=10, sweep_width=.3, 
        sweep_df=.005):
        """

        Args:
        -----
        band (int) : The 500 MHz band to setup.

        Optional Args:
        --------------
        resonance (float array) : A 2 dimensional array with resonance 
            frequencies and the subband they are in. If given, this will take 
            precedent over the one in self.freq_resp.
        drive (int) : The power to drive the resonators. Default 10.
        sweep_width (float) : The range to scan around the input resonance in
            units of MHz. Default .3
        sweep_df (float) : The sweep step size in MHz. Default .005

        Returns:
        --------

        """

        # Check if any resonances are stored
        if 'resonance' not in self.freq_resp[band] and resonance is None:
            self.log('No resonances stored in band {}'.format(band) +
                '. Run find_freq first.', self.LOG_ERROR)
            return

        if resonance is not None:
            input_res = resonance[0,:]
            input_subband = resonance[1,:]
        else:
            input_res = self.freq_resp[band]['resonance'][0]
            input_subband = self.freq_resp[band]['resonance'][1]

        n_subbands = self.get_number_sub_bands(band)
        n_channels = self.get_number_channels(band)
        n_subchannels = n_channels / n_subbands

        # Loop over inputs and do eta scans
        for i, (f, sb) in enumerate(zip(input_res, input_subband)):
            freq, res = fast_eta_scan(band, sb)


    def tracking_setup(self, band, channel, reset_rate_khz=4., write_log=False):
        """
        Args:
        -----
        band (int) : The band number
        channel (int) : The channel to check
        """

        self.set_cpld_reset(1)
        self.set_cpld_reset(0)

        fraction_full_scale = .99

        # To do: Move to experiment config
        flux_ramp_full_scale_to_phi0 = 2.825/0.75

        lms_delay   = 6  # nominally match refPhaseDelay
        lms_gain    = 7  # incrases by power of 2, can also use etaMag to fine tune
        lms_enable1 = 1  # 1st harmonic tracking
        lms_enable2 = 1  # 2nd harmonic tracking
        lms_enable3 = 1  # 3rd harmonic tracking
        lms_rst_dly  = 31  # disable error term for 31 2.4MHz ticks after reset
        lms_freq_hz  = flux_ramp_full_scale_to_phi0 * fraction_full_scale*\
            (reset_rate_khz*10^3)  # fundamental tracking frequency guess
        lms_delay2    = 255  # delay DDS counter resets, 307.2MHz ticks
        lms_delay_fine = 0
        iq_stream_enable = 0  # stream IQ data from tracking loop

        self.set_lms_delay(band, lms_delay, write_log=write_log)
        self.set_lms_dly_fine(band, lms_delay_fine, write_log=write_log)
        self.set_lms_gain(band, lms_gain, write_log=write_log)
        self.set_lms_enable1(band, lms_enable1, write_log=write_log)
        self.set_lms_enable2(band, lms_enable2, write_log=write_log)
        self.set_lms_enable3(band, lms_enable3, write_log=write_log)
        self.set_lms_rst_dly(band, lms_rst_dly, write_log=write_log)
        self.set_lms_freq_hz(band, lms_freq_hz, write_log=write_log)
        self.set_lms_delay2(band, lms_delay2, write_log=write_log)
        self.set_iq_stream_enable(band, iq_stream_enable, write_log=write_log)

        self.flux_ramp_setup(reset_rate_khz, fraction_full_scale, 
            write_log=write_log)

        # self.set_lms_freq_hz(lms_freq_hz)

        self.flux_ramp_on(write_log=write_log)

        self.set_iq_stream_enable(band, 1, write_log=write_log)

    def flux_ramp_setup(self, reset_rate_khz, fraction_full_scale, df_range=.1, 
        do_read=False):
        """
        """
        # Disable flux ramp
        self.set_cfg_reg_ena_bit(0)
        digitizerFrequencyMHz=614.4
        dspClockFrequencyMHz=digitizerFrequencyMHz/2

        desiredRampMaxCnt = ((dspClockFrequencyMHz*10^3)/
            (desiredResetRatekHz)) - 1